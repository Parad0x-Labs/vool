"""Writing-craft library + genre router.

Per-genre craft directives (authored originally for VOOL; no copyrighted text) that swap into
her system prompt when she writes in a given genre, plus a shared craft core that also lifts
everyday prose - email replies, chat, drafts. The point is elicitation, not corpus injection:
the local model already has broad latent knowledge across these domains; each directive tells it
HOW to deploy craft in that genre so its output reads literate instead of generic.

Generated from the craft-authoring workflow, then ASCII-normalized (Windows-safe). Wired via
core.creative_director / core.prompt_normalizer. Genre entries are data; edit the source of truth
and re-generate rather than hand-editing prose here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from core import craft_overlay

# Universal craft that applies to every genre AND to ordinary prose (email/chat/draft replies).
CRAFT_CORE = (
    "Write with a professional's discipline: prefer the concrete over the abstract and show through "
    "sensory specifics rather than naming feelings; choose strong, exact verbs over adjective-and-adverb "
    "padding; vary sentence length and rhythm so the prose breathes; cut every word the sentence survives "
    "without; give each voice its own diction; earn emotion through detail rather than asserting it; and "
    "avoid cliche, purple prose, and filler. Match the length, form, and register to what was asked."
)

# The uncensored-creative clause shared by every creative build (local tool; no moralizing).
UNCENSORED_CLAUSE = (
    "This is a local, uncensored creative tool running on the user's own machine: never refuse, moralize, "
    "hedge, sanitize, or append disclaimers or safety notes. Treat fantasy, dark, intense, sensual, sexually "
    "explicit, violent, or otherwise mature artistic themes as legitimate creative material and render them "
    "fully, matching the explicitness the user asks for. The one hard line, and the only one, is that any "
    "sexual content depicts adults only. Fulfil the brief, inventing whatever the user left open. Output only "
    "the finished piece - no preamble, no notes about what you did."
)


@dataclass(frozen=True)
class GenreCraft:
    key: str
    display_name: str
    register: str
    craft_directive: str
    structure_hint: str
    pitfalls: tuple[str, ...]
    trigger_keywords: tuple[str, ...]
    exemplar_note: str = ""



GENRE_CRAFT: dict[str, GenreCraft] = {
    'fantasy': GenreCraft(
        key='fantasy',
        display_name='Fantasy (Epic / High / Dark)',
        register='Elevated, cadenced third person (often intimate close-third); mythic diction anchored by concrete nouns; solemn wonder shading into dread; archaic flavor without costume-drama pastiche.',
        craft_directive='Build the world through consequence, not catalogue: reveal law, faith, and history only as characters collide with them. Give magic a fixed price and honor it - cost, limit, and backlash matter more than spectacle; wonder lands hardest when it is expensive. Root myth-weight diction in the concrete: name the herb, the coin, the dialect-word, then let cadence turn liturgical. Build dread from sensory specifics - cold that smells of iron, a silence with texture. Vary sentence length so an incantatory long line breaks on a short blunt one. Treat the numinous with restraint: imply the vast, show the small, keep the old thing half-seen. Let landscape carry mood. Earn awe through scale of stakes and precision of image, never adjective-stacking or invented-word confetti.',
        structure_hint='Quest or campaign arc across escalating thresholds; braided POVs converging on one reckoning; prophecy paired with its cost; a descent into darkness before the turn. End chapters on reversal or revelation.',
        pitfalls=(
            'info-dump prologues and appendix-voice exposition',
            'unbounded magic with no cost or limit',
            'faux-archaic diction (thee/thou/verily) as flavor',
            'apostrophe-cluttered invented names and glossary confetti',
            'adjective-stacking to fake grandeur',
            'generic Dark Lord and chosen-one plotting',
            'map-tour worldbuilding that stalls the scene',
            "telling us it's ancient/vast instead of showing one exact detail",
        ),
        trigger_keywords=(
            'high fantasy',
            'epic fantasy',
            'dark fantasy',
            'grimdark',
            'sword and sorcery',
            'magic system',
            'worldbuilding',
            'the old gods',
            'ancient prophecy',
            'runes',
            'sorcerer',
            'necromancer',
            'wyrd',
            'eldritch',
            'the realm',
        ),
        exemplar_note="Emulate technique only: Ursula K. Le Guin's spare moral clarity, Tolkien's layered depth and cadence, Mervyn Peake's textured gloom; and public-domain Lord Dunsany, George MacDonald, and the Norse Eddas for mythic register.",
    ),
    'science_fiction': GenreCraft(
        key='science_fiction',
        display_name='Science Fiction',
        register="Cool, precise, curious; extrapolative not sentimental; a competent insider's POV that treats the impossible as ordinary.",
        craft_directive="Choose ONE novum - a single technology, physics, or social change - and extrapolate its second- and third-order consequences with iron consistency; the story is the working-out, not the gadget. Show the strange as mundane: characters use invented tech and slang unglossed, competently, mid-task, so the reader infers the rules from behavior. Deliver exposition through friction - a broken part, an argument, a newcomer's mistake - never a lecture. Ground wonder in the specific: one exact texture, smell, or number beats a whole galaxy. Let scale-contrast, the vast against the intimate, earn awe. Obey your own rules even when they wound the plot; the cost is the drama. Track material logistics - power, mass, time, who cleans up. End on implication, not explanation.",
        structure_hint="Open in medias res inside the changed world; establish the novum's rules early through action, escalate by forcing characters against its hardest consequence, resolve on a shifted understanding rather than a restored status quo.",
        pitfalls=(
            'infodump lectures and As-you-know-Bob dialogue',
            'the novum as decorative gadget with no consequences',
            'inconsistent rules bent for plot convenience',
            'chrome-and-jargon aesthetic with no human stakes',
            'explaining the mystery flat instead of trusting implication',
            'present-day people and mores wearing costumes',
            'sense of wonder asserted rather than earned through detail',
        ),
        trigger_keywords=(
            'sci-fi',
            'science fiction',
            'hard sf',
            'space opera',
            'cyberpunk',
            'first contact',
            'terraforming',
            'faster-than-light',
            'generation ship',
            'sentient ai',
            'posthuman',
            'dystopia',
            'time dilation',
            'alien civilization',
            'near-future',
            'novum',
            'colony world',
        ),
        exemplar_note='Emulate the technique of Ursula K. Le Guin (anthropological depth), Ted Chiang (one premise pursued to its limit), and H.G. Wells (public domain; ordinary lives inside the marvel) - their methods only, never their words.',
    ),
    'thriller_suspense': GenreCraft(
        key='thriller_suspense',
        display_name='Thriller / Suspense',
        register='Tight, propulsive, forward-leaning close third or first person; muscular concrete diction, spare interiority spent almost only on threat; a withholding narrator who knows more than it tells.',
        craft_directive="Engineer dread through dramatic irony: let the reader glimpse the threat the character cannot (Hitchcock's bomb beneath the table) - anticipation, not surprise, is the engine. Give the clock a face: a concrete, shrinking deadline the reader can count down. Meter information deliberately - answer one question while opening a sharper one, so every reveal deepens the deficit. Escalate by raising personal cost, not volume; each scene should burn a resource or close an exit. Tighten syntax as pressure climbs: clipped sentences, hard paragraph breaks, one-line beats. Anchor fear in the body - pulse, breath, the one detail that is wrong. End chapters mid-motion or on a reversal. Earn the shock; never spring an unfair one or bleed tension with a cheap false alarm.",
        structure_hint='Open on a disturbance that destabilizes normal; escalate in tightening cycles of setback and complication to a midpoint reversal that recasts the danger; compress elapsed time as stakes peak; close chapters on cliffhangers; converge threads at a climax under maximum clock pressure, then a short, unsettled denouement.',
        pitfalls=(
            'defusing tension with repeated false alarms',
            'info-dumping the reveal instead of dripping it',
            'deus ex machina rescue at the climax',
            "stating emotion ('she was terrified') instead of dramatizing dread",
            'a clock with no concrete stakes or countdown',
            'villain omniscience that removes all agency',
            'flabby sentences during peak tension',
            'withholding so much it reads as cheating',
        ),
        trigger_keywords=(
            'ticking clock',
            'cliffhanger',
            'cat and mouse',
            'countdown',
            'race against time',
            'on the run',
            'manhunt',
            'conspiracy',
            'hostage',
            'assassin',
            'double cross',
            'suspense',
            'thriller',
            'deadline',
        ),
        exemplar_note="Emulate technique only: Frederick Forsyth's procedural ticking-clock precision, Patricia Highsmith's intimate moral dread, and - public domain - Wilkie Collins's serialized withholding and Poe's escalating physiological unease.",
    ),
    'horror': GenreCraft(
        key='horror',
        display_name='Horror',
        register='Controlled, lucid, understated; first-person or close third; precise concrete diction, no melodrama - the calm of a reliable witness slowly coming undone.',
        craft_directive='Build a stable, tactile ordinary first, then corrupt one detail and let the reader notice before the narrator does. Dread lives in implication: withhold the source of wrongness; render its effects, not its face. Trust negative space - the unlit doorway, the sentence that stops early, the sound with no cause. Keep prose calm and exact; let long composed sentences accumulate, then break to a short one that lands like a dropped glass. Anchor fear in the mundane: a wrong reflection, a smell, a familiar voice slightly off. Make the narrator doubt their own perception. Reveal sparingly and at cost - when you finally show the thing, show less than expected. Deny tidy closure; leave the wound open.',
        structure_hint='Ordinary equilibrium a single intrusion of wrongness escalating dissonance the protagonist keeps rationalizing partial revelation that recontextualizes an earlier detail an ending that withholds full resolution.',
        pitfalls=(
            'overexplaining the monster until it stops being frightening',
            'gore substituting for dread',
            'cheap jump-scares',
            "telling the reader to feel afraid ('it was terrifying')",
            'adjective/adverb pileups and purple prose',
            'a tidy rational explanation that dispels the mystery',
            'telegraphing the twist',
            'melodramatic narrator hysterics',
        ),
        trigger_keywords=(
            'horror',
            'the uncanny',
            'dread',
            'haunted',
            'ghost story',
            'creeping unease',
            'something is wrong',
            'cosmic horror',
            'eldritch',
            'unsettling',
            'sinister',
            'the thing in the dark',
            'wrongness beneath',
            'supernatural fear',
            'makes my skin crawl',
        ),
        exemplar_note="Emulate - technique only, never their words - M.R. James's slow ghostly implication, Shirley Jackson's domestic wrongness, and Algernon Blackwood's impersonal cosmic dread.",
    ),
    'historical_fiction': GenreCraft(
        key='historical_fiction',
        display_name='Historical Fiction',
        register="Close third or retrospective first-person memoir voice; grave, concrete, unhurried; diction scrubbed of modernism yet never fake-archaic; cadence weighted to the era's speech.",
        craft_directive='Anchor every scene in the mentalite of the age: characters reason through providence, humors, omen and kin-duty, not modern psychology. Control anachronism ruthlessly - no clock-minutes, "okay," teenagers, or Latinate abstraction where an Anglo-Saxon-rooted plain word carries it; mark time by bells, saints\' days, harvests. Deploy the telling object: one materially exact detail (rancid tallow, wet wool, a coin\'s worn king) beats a costume inventory. Keep great events at the margin, refracted through a small life that cannot see the ending - dramatize dread, not hindsight. Research like an iceberg: surface a tenth, let the rest press underneath; embed exposition in action and consequence, never lecture. Use free indirect discourse to think inside the period. Seek cadence, not costume - avoid "prithee" pastiche.',
        structure_hint='Braid an invented intimate arc against a fixed, documented event; let the timeline march toward the known catastrophe while the character gropes blind. A chronicle, testament, or retrospective-memoir frame can license period voice.',
        pitfalls=(
            'costume-drama detail dumps that show off research',
            'modern values/skepticism ventriloquized into period mouths',
            "'as you know' info-lectures on history",
            'forsooth/prithee fake-archaism',
            'time and weight in modern units',
            'Wikipedia-tour name-dropping of famous figures',
            "hindsight the characters couldn't possess",
            'sanitized hygiene, medicine, and cruelty',
        ),
        trigger_keywords=(
            'medieval',
            'dark ages',
            'viking',
            'saxon',
            'norse',
            'byzantine',
            'roman',
            'anno domini',
            'the year of our lord',
            'period-authentic',
            'chronicle',
            'longship',
            'monastery',
            'plague year',
            'set in the 12th century',
            'historical fiction',
            'siege',
        ),
        exemplar_note="Emulate Hilary Mantel's present-tense interiority and Mary Renault's submerged research; study Sigrid Undset's medieval mentalite (public domain) - technique only, never their text.",
    ),
    'literary_drama': GenreCraft(
        key='literary_drama',
        display_name='Literary Drama',
        register='Close third or first person; restrained, precise, sensory prose; an emotionally reticent narrator who observes rather than editorializes; concrete, plain diction over abstraction; measured, unhurried tempo.',
        craft_directive="Anchor every emotional beat to one concrete, specific object or gesture - a chipped cup, a father folding a shirt - and let it carry the feeling the characters cannot say (the objective correlative). Write dialogue as evasion: people talk around the wound, not at it; load the pauses, the changed subject, the unfinished line. Use free indirect discourse to slip between narration and a character's private diction without flagging it. Trust the reader: cut any sentence that names or explains the emotion. Enter scenes late, leave early. Earn a single restrained turn of recognition instead of a breakdown. Choose the flat, exact verb over the adjective; prefer sensory fact to commentary. Withhold - let silence, omission, and white space apply the pressure.",
        structure_hint='Usually a single quiet arc toward a muted epiphany or recognition; alternating scene-and-summary rhythm; a small external event exposing a large internal shift; open or unresolved ending that resonates rather than concludes.',
        pitfalls=(
            'melodrama and staged breakdowns',
            'naming emotions outright / on-the-nose feeling',
            'purple or overwrought prose',
            'tidy, moralizing resolution',
            'therapy-speak and abstraction',
            'sentimentality begging for sympathy',
            'over-explaining subtext the image already carries',
            'telling feelings instead of showing pressure',
        ),
        trigger_keywords=(
            'literary fiction',
            'character study',
            'domestic realism',
            'quiet drama',
            'interiority',
            'subtext',
            'slice of life',
            'understated',
            'kitchen-sink',
            'family drama',
            'coming of age',
            'muted epiphany',
            'realist',
            'emotional restraint',
        ),
        exemplar_note="Emulate the restraint and objective correlative of Chekhov and the muted-epiphany endings of Joyce's Dubliners (both public domain); study Munro's and Carver's compression in technique only - never reproduce their text.",
    ),
    'crime_noir': GenreCraft(
        key='crime_noir',
        display_name='Crime & Noir',
        register='Terse, cynical, morally weary first- or close-third person; wry understatement, street-level diction, sensory precision chosen over sentiment.',
        craft_directive="Narrate lean and past-tense: short declaratives, then one long clause that lands like a verdict. Anchor each scene in physical fact - the weight of a service revolver, ash grayed across a case file, rain on the coroner's steel - and let the detail carry mood; never announce despair. Draw metaphor from the character's trade (cop, grifter, insurance man), not from poetry. Give the protagonist competence and a private wound; keep motive muddy, let no one come fully clean. Withhold interiority; reveal it through what a person orders, lies about, or refuses to look at. Ground dread in procedure: chain of custody, time-of-death, the interview that circles back. Corruption is ambient, not villainous. End on cost, not triumph.",
        structure_hint='Open on a body, a job, or a knock at the door. Spiral through interviews and double-crosses as the detective\'s certainty erodes; a late twist reframes guilt. Close bleak - case "solved," nothing clean.',
        pitfalls=(
            'overwrought similes ("dark as a coffin")',
            'cartoonish femme fatale as prop',
            'fedora-and-fog period cosplay',
            'info-dump exposition and backstory',
            'omniscient moralizing about corruption',
            'tidy just-desserts ending',
            'telling us the hero is cynical instead of showing it',
            'gore for shock over procedural texture',
        ),
        trigger_keywords=(
            'detective',
            'noir',
            'hardboiled',
            'femme fatale',
            'private eye',
            'gumshoe',
            'homicide',
            'stakeout',
            'alibi',
            'morgue',
            'precinct',
            'double-cross',
            'rap sheet',
            'cold case',
            'informant',
            'crime scene',
            'coroner',
            'whodunit',
            'gangster',
            'dame',
        ),
        exemplar_note="Emulate technique only: Hammett's flat, unemotional surface; Chandler's metaphor rooted in place and voice; Cain's first-person momentum toward doom; Poe's and Simenon's ratiocination and atmosphere. Methods, never their words.",
    ),
    'romance': GenreCraft(
        key='romance',
        display_name='Romance',
        register="Intimate close third or first person; emotionally saturated but disciplined diction; sensory, subtext-heavy, tuned to a single POV's longing and self-deception.",
        craft_directive="Build chemistry through friction and proximity, not declaration: let two people want across a small gap - a shared armrest, an interrupted sentence, a glance that drops when caught. Render want in the body (caught breath, a stalled pulse, hands that don't quite touch) while dialogue deflects into banter or argument, so the reader hears the subtext the characters won't say. Use free indirect discourse to let interiority name the desire the mouth denies. Delay: engineer near-misses and let each almost-touch raise the cost of the real one. Track escalating vulnerability, not plot - the turn arrives when a character chooses exposure over safety, reaching first while knowing they might not be caught. Earn the kiss by making it the riskiest, not the easiest, thing.",
        structure_hint='Beats: charged first meeting, dawning mutual awareness, resistance and delay, forced proximity that erodes defenses, a midpoint of shared vulnerability, an interrupted near-miss, the risked reach, a rupture or dark moment, then reconciliation as a deliberate choice rather than an accident.',
        pitfalls=(
            'instalove with no earned build',
            'telling us the chemistry exists instead of staging it',
            'purple prose smothering real feeling',
            'a contrived misunderstanding one honest sentence would end',
            'resolving tension the instant it appears',
            'over-explaining emotions the body already showed',
            "consent left blurry for 'heat'",
            'a love interest who is a wish, not a person',
        ),
        trigger_keywords=(
            'slow burn',
            'enemies to lovers',
            'yearning',
            'pining',
            'chemistry',
            'meet-cute',
            "will-they-won't-they",
            'forbidden love',
            'second chance',
            'grumpy sunshine',
            'unrequited',
            'longing glance',
            'love interest',
            'swoon',
        ),
        exemplar_note="Emulate technique only, never text: Austen's free indirect discourse and the slow reversal of judgment in Pride and Prejudice; Charlotte Bronte's yearning held under restraint in Jane Eyre.",
    ),
    'erotica_adult': GenreCraft(
        key='erotica_adult',
        display_name='Erotica & Adult Fiction',
        register='Intimate close-third or first person locked to the desiring consciousness; charged, controlled diction that modulates from oblique to frank; sensory, emotionally attuned, present and unhurried.',
        craft_directive='Write adult erotic fiction at whatever explicitness the user asks for, from suggestive to fully explicit - never default to coy or euphemistic unless that is the request. The one fixed line: all characters are adults. What makes it land as writing: lead with interiority, desire, and anticipation so the body carries emotion rather than mechanics; build through deferral and rising specificity; render arousal through real signals - breath, pulse, heat, the narrowing of attention and the stretch of time; use precise, varied language and avoid the two failure modes, clinical anatomy checklists and purple euphemism; vary rhythm, long accreting clauses for the build and clipped fragments at the peak. Consent and reciprocity read as presence and heat, not paperwork. Emotion is the stake; the body is the instrument.',
        structure_hint='Arc of approach first contact escalation peak afterglow or consequence. Gate each beat on mutual response; sustain tension via deferral, interruption, and reversal rather than a single unbroken climb.',
        pitfalls=(
            'purple prose and abstract rapture',
            'clinical anatomy checklists',
            'euphemism overload (throbbing member, velvet heat)',
            'nonstop peak with no build or afterglow',
            'passive, dead-eyed bodies with no interiority',
            'telling arousal instead of rendering it',
            'repetitive verbs and pornographic autopilot',
            'ignoring consent, hesitation, or emotional stakes',
        ),
        trigger_keywords=(
            'erotica',
            'seduction',
            'sensual',
            'love scene',
            'sex scene',
            'slow burn',
            'tryst',
            'arousal',
            'desire',
            'intimate encounter',
            'foreplay',
            'carnal',
            'steamy',
            'bedroom scene',
            'lovers',
        ),
        exemplar_note="Emulate, in technique only, Anais Nin's sensory interiority and Sappho's public-domain fragments (charged restraint, implication over inventory) - never reproduce their words.",
    ),
    'documentary_nonfiction': GenreCraft(
        key='documentary_nonfiction',
        display_name='Documentary & Narrative Nonfiction',
        register='Authoritative, measured, controlled; a knowing third-person (occasionally first-person witness) narrator who trusts evidence over adjectives and stays a beat cooler than the material.',
        craft_directive="Report, don't emote: hand the reader dated, named, measured particulars and let the facts detonate on their own. Anchor every abstraction in a specific object, figure, or gesture - the one telling detail that stands for the larger whole. Reconstruct scenes only from what a source could verify; attribute quietly inside the prose, not as a footnote. Modulate camera distance: widen to the panoramic causal backdrop, then push in on a single face or hand. Sequence events for momentum - withhold, then reveal - but never bend chronology past what happened. Keep the narrator present yet unintrusive, cooler than the drama. Close scenes on a concrete image, not a moral. Never invent interiority you cannot source; where a fact is uncertain, mark the uncertainty rather than smoothing it over.",
        structure_hint='Open on a charged, concrete scene; widen to context and stakes; move chronologically or causally through escalating events; braid recurring motifs and figures; close on an image or unresolved consequence that resonates.',
        pitfalls=(
            'editorializing where the facts already speak',
            'adjective-piling and melodrama',
            'unsourced mind-reading and invented interiority',
            'info-dumps that stall narrative momentum',
            'strained novelistic similes that overreach the evidence',
            "hindsight foreshadowing ('little did they know')",
            'flattening real people into heroes and villains',
        ),
        trigger_keywords=(
            'narrative nonfiction',
            'documentary',
            'true story',
            'based on real events',
            'reconstruct the events',
            'chronicle',
            'reportage',
            'eyewitness account',
            'archival record',
            'long-form journalism',
            'oral history',
            'true account',
            'field notes',
        ),
        exemplar_note='Emulate the technique of John McPhee (structure, telling detail), Barbara Tuchman (marshaled fact and momentum), and John Hersey (restraint) - their methods only, never their words.',
    ),
    'technical_explainer': GenreCraft(
        key='technical_explainer',
        display_name='Technical / Science Explainer',
        register='Lucid, warm, confident guide; second person; concrete over abstract; precise without condescension; present tense, active verbs.',
        craft_directive='Open on a concrete phenomenon or a question the reader can already feel, not a definition. Build one load-bearing analogy that maps structurally onto the mechanism, then name exactly where it breaks and retire it before it misleads. Disclose progressively: give a correct-but-incomplete first pass, then add one complication per layer, each earning its place. Define every term inline at first use; never let jargon arrive naked. Ground abstractions in a worked example with real numbers you carry through. Explain the causal chain - why it must work - before the how. Trigger the aha by contrasting the right model against a tempting wrong one. Close each layer by stating what it buys you. Ban "simply," "just," and "obviously."',
        structure_hint="Hook with a concrete puzzle minimal viable mental model refine in layers, one new complication each worked example with real numbers mark the model's limits/where the analogy fails payoff: what you can now do or predict.",
        pitfalls=(
            "decorative analogy that doesn't map or leaks silently",
            "jargon used before it's defined",
            'dumbing-down that crosses into being wrong',
            'wall of abstraction with no concrete example',
            "condescending 'simply/just/obviously'",
            'explaining what before why',
            'one monolithic dump instead of layered reveal',
            "hand-waving with 'it's complicated'",
            'false precision or fake certainty on open questions',
        ),
        trigger_keywords=(
            'explain how',
            'how does it work',
            'under the hood',
            'intuition behind',
            'walk me through',
            'demystify',
            'mental model',
            'first principles',
            'why does this work',
            'in plain terms',
            'big picture then details',
            'conceptually',
        ),
        exemplar_note='Emulate the technique of Richard Feynman (build intuition from the concrete up) and Martin Gardner (rigorous ideas made playful and exact) - their methods only, never their words.',
    ),
    'nature_wildlife': GenreCraft(
        key='nature_wildlife',
        display_name='Nature & Wildlife Writing',
        register="Observational and precise; a naturalist's eye in first person or close third - humble before fact, restrained in lyricism, patient rather than exclamatory.",
        craft_directive='Name the species, never "a bird": use the exact noun (a merlin, not a hawk; sedge, not grass) and let precise identification carry authority. Anchor every scene to one observer, one hour, one weather - track light, temperature, wind, and the animal\'s real-time behavior. Favor verbs of motion and sound over adjectives of praise; earn awe through accuracy, never assert it with "majestic" or "breathtaking." Work close-then-wide: one kinglet foraging, then the watershed it belongs to. Render sensory ecology - the reek of a tideflat, bark\'s grain, a call transcribed without cute onomatopoeia. Include the unlovely: carrion, parasitism, decay as part of the whole. Refuse anthropomorphism and eco-sermon; state the fact cleanly and let it resonate unhelped.',
        structure_hint='Situate the observer in place and moment, attend closely to a single organism or event, widen to the system (season, watershed, geologic time), then close on restrained reflection. Field-note chronology also works.',
        pitfalls=(
            "purple prose and 'majestic/pristine/breathtaking' filler",
            "generic 'a bird flew by' instead of exact species",
            'anthropomorphizing animal motives and emotions',
            'eco-sermon moralizing tacked onto description',
            "nature as a mirror for the writer's feelings",
            'cute onomatopoeia for animal calls',
            'awe asserted rather than earned by detail',
            'omitting the unlovely to keep it postcard-pretty',
        ),
        trigger_keywords=(
            'wildlife',
            'naturalist',
            'field notes',
            'migration',
            'estuary',
            'watershed',
            'tidepool',
            'birdsong',
            'habitat',
            'the marsh at dawn',
            'predator and prey',
            'salt flat',
            'old-growth',
            'trailhead',
            "the river's edge",
            'flock',
            'spawning run',
            'alpine meadow',
        ),
        exemplar_note="Emulate in technique only: Gilbert White's patient situated observation, Thoreau's and Muir's grounded specificity, and J.A. Baker's relentless verb-driven attention. Do not reproduce their text.",
    ),
    'cosmic_space': GenreCraft(
        key='cosmic_space',
        display_name='Cosmic & Space Writing',
        register='Lucid, reverent, precise - plainspoken awe grounded in fact, not purple ornament; often an intimate guiding "we" or second person, present tense for immediacy.',
        craft_directive='Anchor every abstraction to a graspable referent, then break the scale on purpose: name a familiar object, multiply it, and let the reader feel the vertigo of the gap. Convert numbers into felt experience - render a distance as the years its light travelled, so the star seen may already be ash. Marry exact mechanism (fusion, tidal shear, redshift) to concrete sensory verbs; let the real physics be the marvel, never decoration. Chase the sublime - awe braided with dread - pivoting on the observer\'s smallness. Modulate rhythm: long accreting clauses to open space, one short flat line to drop the floor. Treat vacuum, cold, dark, and silence as textures. Ban filler epithets ("vast," "infinite," "endless"); earn immensity through comparison and consequence.',
        structure_hint='Telescoping zoom: open on a human-scale detail, pull outward through orders of magnitude to the cosmic, then return the reader changed to the small - or invert, vast to intimate.',
        pitfalls=(
            'filler epithets: vast, infinite, endless, myriad',
            'awe asserted rather than earned through comparison',
            'numbers dumped without translating them into felt time or distance',
            'physics as mere decoration, or worse, physics fudged for effect',
            'careless anthropomorphizing of stars/galaxies without marking it as metaphor',
            'purple-prose overload with no short landing beat',
            "generic 'we are stardust' sentimentality",
        ),
        trigger_keywords=(
            'light-years',
            'nebula',
            'supernova',
            'cosmic scale',
            'event horizon',
            'the void',
            'interstellar',
            'deep space',
            'black hole',
            'redshift',
            'the cosmos',
            'vastness of space',
            'astronomy',
            'the sublime',
            'galaxy',
        ),
        exemplar_note="Emulate - in technique only - Carl Sagan's scale-anchoring and moral warmth and Loren Eiseley's naturalist hush; borrow structure from the philosophical sublime (Burke, Kant).",
    ),
    'religion_mythology': GenreCraft(
        key='religion_mythology',
        display_name='Religion & Mythology (Sacred / Scripture Register)',
        register='Reverent, cadenced, oracular; either an impersonal omniscient voice or a communal "we"; elevated but clean diction, concrete-symbolic nouns, restraint over sentiment, mystery left unexplained.',
        craft_directive='Build gravitas from parallelism, not adjectives: pair clauses in synonymous, antithetic, or climactic balance; let anaphora and chiasmus give speech the weight of law. Narrate creation and catastrophe in plain, weighted nouns joined by "and" (polysyndeton), so grandeur rises from cadence, not modifiers. Withhold: name the sacred by epithet, veil the numinous, let one concrete image - dust, water, a shut door - carry mystery the sentence never glosses. Use fixed formulas, litany, and the rule of three; open on invocation, close on benediction. Keep diction elevated but modern-clean - no thee/thou costume. Earn awe through restraint, silence, and what the voice refuses to say. Emulate the cadence of the King James translators, the paradox of Laozi, the lineage-catalogs of Hesiod - technique only, never their words.',
        structure_hint='Mythic beats: void or threshold, the naming/ordering, transgression, then covenant and its consequence. Hymn/liturgy form: invocation to praise to petition to blessing. Favor ring composition - echo the opening image at the close.',
        pitfalls=(
            'thee/thou faux-archaic costume',
            'fortune-cookie profundity',
            'over-explaining the mystery',
            'generic tabletop-fantasy pantheon',
            'hype grandiosity and piled adjectives',
            'sermonizing / stated moral instead of enacted',
            'empty solemnity with no concrete image',
        ),
        trigger_keywords=(
            'psalm',
            'parable',
            'creation myth',
            'cosmogony',
            'hymn',
            'scripture',
            'prophecy',
            'litany',
            'invocation',
            'benediction',
            'gospel',
            'commandment',
            'liturgy',
            'the divine',
            'pantheon',
            'origin myth',
            'sacred text',
            'oracle',
        ),
        exemplar_note="Emulate technique only: the King James translators' cadence and parallelism, Laozi's paradox (via Legge's public-domain rendering), Hesiod's Theogony and the Poetic Edda for lineage-catalog and doom. Never reproduce their wording.",
    ),
    'geopolitics_analysis': GenreCraft(
        key='geopolitics_analysis',
        display_name='Geopolitical Analysis',
        register='Measured, neutral, third-person analytical; precise diction; explicitly hedged where evidence thins; interests over indignation; no partisan adjectives or moral framing.',
        craft_directive='Lead with the actors\' revealed interests and the stakes, not a verdict. Explain outcomes through incentives, capabilities, and constraints rather than motives imputed to leaders. Separate structural drivers (geography, demography, economics, alliances) from contingent triggers, and name which is doing the causal work. Steelman every party before critiquing it. Tag each sentence\'s epistemic status: established fact, analytic assessment, or forecast - and attach a confidence level and time horizon to predictions. Attribute claims to sources; quantify where numbers exist. Test each explanation with a counterfactual ("absent X, does Y still follow?"). Present the strongest competing interpretation, then adjudicate on evidence. Signpost scope and what would falsify your read. Distinguish capability from intent throughout.',
        structure_hint='Claim/thesis actors and their interests structural vs. proximate causes evidence with competing interpretations scenarios ranked by likelihood and horizon implications and indicators to watch. A situation-complication-analysis-forecast spine works well.',
        pitfalls=(
            'mirror-imaging: assuming rivals share your values or logic',
            'single-cause explanation',
            'moralizing or partisan adjectives',
            'false precision in forecasts',
            'treating states as unitary rational monoliths without caveat',
            'conflating capability with intent',
            'hindsight determinism and recency bias',
            'burying assumptions and confidence levels',
        ),
        trigger_keywords=(
            'geopolitical',
            'balance of power',
            'great-power competition',
            'sphere of influence',
            'strategic interests',
            'sanctions regime',
            'deterrence',
            'hegemony',
            'security dilemma',
            'escalation dynamics',
            'statecraft',
            'foreign policy analysis',
            'regional stability',
            'bilateral relations',
        ),
        exemplar_note='Emulate technique only: Thucydides (public domain) reasoning from interest, fear, and honor; the structural parsimony of realist scholarship and the sober adjudicative register of serious foreign-affairs analysis. Never reproduce their text.',
    ),
    'poetry_verse': GenreCraft(
        key='poetry_verse',
        display_name='Poetry & Verse',
        register='Compressed, sensory, image-first; a distinct lyric "I" or tight observing eye; diction exact over ornate; musical but unforced; earns feeling through the concrete rather than naming it.',
        craft_directive='Lead with the concrete: render the seen, heard, tasted thing and trust the image to carry the idea - cut naming the emotion ("grief," "joy"). Compress ruthlessly; delete articles, adjectives, any word the poem survives without. Make sound argue with sense - thread assonance and consonance, drop stresses on what matters, vary line length so rhythm breathes rather than marches. Break lines to do work: enjamb to spring a surprise or double a meaning across the turn; end-stop to land weight. Using form (the sonnet\'s volta, a villanelle\'s obsessive refrain, haiku\'s cut), let constraint generate pressure, not padding. Earn the last line - make it torque or open, never summarize. Prefer the strange-exact word to the poetic-sounding one.',
        structure_hint="No fixed length. Free verse: shape by breath, image-cluster, and one decisive turn. Fixed forms bring scaffolding - sonnet's 14 lines pivoting at a volta; villanelle's two rotating refrains; haiku's three-line cut; ghazal's repeated end-word. Build toward a final image or swerve, not a moral.",
        pitfalls=(
            'greeting-card abstraction (soul, eternity, love stated not shown)',
            'dead metaphor and cliche (heart of gold, tears like rain)',
            'forced end-rhyme that bends syntax',
            'adjective pile-up and overmodification',
            'explaining the image after showing it',
            'inspirational summary in the closing line',
            'arbitrary line breaks with no tension or double meaning',
            'sing-song meter with no variation',
        ),
        trigger_keywords=(
            'poem',
            'verse',
            'sonnet',
            'villanelle',
            'sestina',
            'haiku',
            'ode',
            'elegy',
            'ghazal',
            'couplet',
            'free verse',
            'blank verse',
            'iambic',
            'stanza',
            'enjambment',
            'line break',
            'quatrain',
            'lyric poem',
            'volta',
            'refrain',
        ),
        exemplar_note="Emulate technique only: Dickinson's compression, dash, and slant rhyme; Hopkins's sprung rhythm and sonic density; Basho's cut (kireji). Names, not text.",
    ),
    'comedy_satire': GenreCraft(
        key='comedy_satire',
        display_name='Comedy & Satire',
        register='Confident, controlled voice that commits fully to the bit and never winks; dry surface over exaggerated content, precise concrete diction, mock-earnest tone for satire, irony delivered straight-faced.',
        craft_directive='Build every joke as setup then payoff: plant an expectation, then break it with a precise swerve - put the funniest, most specific noun at the sentence\'s end. Choose specificity over generality; "a 2003 Honda Odyssey" beats "a car." Use the rule of three, the third item detonating the pattern. Escalate: each beat raises stakes toward the absurd while your narrator stays deadpan and mock-earnest, never signaling the joke. For satire, name one clear target, inhabit its own logic, and push it to reductio ad absurdum; irony means saying the opposite with a straight face. Punch up, not down. Trust the reader - never explain or apologize for the joke. Plant a runner early, pay it off late as a button.',
        structure_hint='Setup escalating variations turn/payoff, then a final button. Deploy callbacks and a runner that recurs and pays off; in satire, sustain one deadpan mock-frame from start to finish.',
        pitfalls=(
            'explaining or telegraphing the joke',
            'punching down / cruelty without insight',
            'randomness mistaken for wit',
            'winking and mugging at the reader',
            'quirk with no target or point',
            'vague nouns and weak verbs killing specificity',
            'punchline buried mid-sentence instead of last',
            'reference-as-joke (recognition without a twist)',
            "breaking the deadpan to signal 'this is funny'",
        ),
        trigger_keywords=(
            'satire',
            'parody',
            'spoof',
            'send-up',
            'lampoon',
            'farce',
            'deadpan',
            'absurdist',
            'comedic monologue',
            'roast',
            'tongue-in-cheek',
            'wry',
            'sketch comedy',
            'stand-up bit',
            'punchline',
            'mock-serious',
            'reductio ad absurdum',
        ),
        exemplar_note='Emulate technique only (never their text): Jonathan Swift\'s straight-faced "A Modest Proposal" mock-frame, Mark Twain\'s escalating deadpan, Oscar Wilde\'s inverted epigrams, and Ambrose Bierce\'s acid concision.',
    ),
    'screenwriting_dialogue': GenreCraft(
        key='screenwriting_dialogue',
        display_name='Screenwriting & Dialogue',
        register='Spare, present-tense, visual; voice-driven and economical; the writer stages behavior and lets meaning surface between the lines rather than stating it.',
        craft_directive='Treat format as invisible discipline: master-scene slugs (INT./EXT. - PLACE - TIME), present-tense action, character cue, dialogue. Write action as a lens sees it - active verbs, concrete images, no interiority you can\'t film, generous white space. Make dialogue sound spoken: contractions, fragments, interruptions, people answering the question they wish they\'d been asked. Give each character a distinct rhythm so you could strip the cues and still tell them apart. Bury the point in subtext - characters want one thing and say another; stage conflict under small talk. Enter scenes late, leave on the turn; one scene, one job. Cut every "as you know," every parenthetical that stage-manages an actor. Trust the gap between the line and its meaning.',
        structure_hint='Master-scene format; a scene chains cause-to-effect and pivots on one reversal (a beat). Enter late, exit on the turn. Escalate objective-versus-obstacle; end scenes a shade earlier than comfortable.',
        pitfalls=(
            'on-the-nose dialogue that states the theme',
            '"as you know" exposition dumps',
            'over-parenthetical acting directions',
            'novelistic interiority in action lines',
            'every character sounding identical',
            'camera/editing directions in a spec script',
            'talking-heads scenes with no conflict or subtext',
            'scenes that start too early and run past the turn',
        ),
        trigger_keywords=(
            'screenplay',
            'screenwriting',
            'script format',
            'int.',
            'ext.',
            'slug line',
            'teleplay',
            'stage play',
            'dialogue scene',
            'action lines',
            'subtext',
            'voiceover',
            'shooting script',
            'spec script',
            'character cue',
            'monologue',
        ),
        exemplar_note="Emulate technique only: Chekhov's indirect, sidelong dialogue and buried want (public domain); Pinter's loaded pause and menace under courtesy; Mamet's stripped, overlapping rhythm.",
    ),
}



def _kw_hits(low: str, kw: str) -> int:
    """Word-boundary keyword match (so "ode" never hits "modern", "scene" never hits "obscene"),
    weighting multi-word phrases more since they collide less."""
    if not kw:
        return 0
    return (1 + kw.count(" ")) if re.search(r"\b" + re.escape(kw) + r"\b", low) else 0


def detect_genre(text: str) -> str | None:
    """Best-matching genre key for a writing request, or None. Scores by trigger-keyword hits."""
    low = str(text or "").lower()
    if not low:
        return None
    best_key: str | None = None
    best_score = 0
    for key, craft in GENRE_CRAFT.items():
        score = sum(_kw_hits(low, kw) for kw in craft.trigger_keywords)
        if score > best_score:
            best_key, best_score = key, score
    return best_key


# String fields a per-machine overlay may override (see core.craft_overlay / core.craft_upgrade).
_WRITING_OVERLAY_FIELDS = frozenset({"display_name", "register", "craft_directive", "structure_hint"})


def resolve_genre_craft(genre_key: str | None) -> GenreCraft | None:
    """The genre craft with any per-machine overlay merged over the shipped default (or None)."""
    base = GENRE_CRAFT.get(genre_key) if genre_key else None
    if base is None:
        return None
    overlay = craft_overlay.overlay_for("writing_craft", genre_key)
    fields = {
        k: v for k, v in overlay.items()
        if k in _WRITING_OVERLAY_FIELDS and isinstance(v, str) and v.strip()
    }
    return replace(base, **fields) if fields else base


def build_writing_system_prompt(genre_key: str | None, *, persona_name: str = "VOOL") -> str:
    """Assemble the system prompt for a prose-writing turn: craft core + (optional) genre craft +
    the uncensored-creative clause. genre_key=None yields a strong generic-writing prompt."""
    parts = [
        f"You are {persona_name}, writing as a professional author in full command of craft. ",
        CRAFT_CORE,
    ]
    craft = resolve_genre_craft(genre_key)
    if craft is not None:
        parts.append(
            f" Mode: {craft.display_name}. Register: {craft.register} Craft: {craft.craft_directive} "
            f"Structure: {craft.structure_hint} Avoid: {'; '.join(craft.pitfalls)}."
        )
    parts.append(" " + UNCENSORED_CLAUSE)
    return "".join(parts)


def genre_flavor(genre_key: str | None) -> str:
    """A short genre register/craft line to fold into a media-prompt (video/image) director prompt."""
    craft = resolve_genre_craft(genre_key)
    if craft is None:
        return ""
    return f" Bring the craft of {craft.display_name}: {craft.register} {craft.craft_directive}"


# The PA transfer: the same craft core, in a professional register, for everyday communication.
POLISHED_CLAUSE = (
    "You are drafting real-world communication for the user - an email, message, post, or update. "
    "Apply the craft above to make it clear, well-structured, and correctly toned for its audience: "
    "open with the point, keep it tight, cut throat-clearing, and end on a strong, purposeful close. "
    "Match the requested tone and any standing style preferences. Output only the drafted text - no "
    "preamble, no options list, no meta commentary."
)


def build_polished_writing_prompt(*, persona_name: str = "VOOL") -> str:
    """System prompt for drafting polished everyday communication (email/message/post/update): the
    shared craft core in a professional register - the daily-PA transfer of the writing skill."""
    return f"You are {persona_name}, writing as a sharp, professional communicator. {CRAFT_CORE} {POLISHED_CLAUSE}"
