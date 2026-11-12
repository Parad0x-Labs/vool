---
name: vool-create-pet
description: "Create an original pixel pet for Toybox and Pet Yard, with distinct reactions and local voice assignments. Use for an explicit request to create a VOOL pet."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
id: create-pet
risk-class: workspace_write
task-families: []
triggers: [create a pet, make a pet, make me a pet, design a pet, new pet, my own pet, pet pack, pixel pet, toybox pet, pet yard, pet for the yard, voolpet]
capability-families: [workspace]
tool-intents: [workspace.read_file, workspace.write_file, sandbox.run_command]
permitted-tools: [workspace.read_file, workspace.write_file, workspace.ensure_directory, sandbox.run_command]
prerequisites: []
expected-outputs: [pet_pack, sprite_preview, import_instructions, verification_statement]
verification: [typed_receipts, cumulative_suite_green]
stopping-conditions: ["Stop after the pack validates, its preview is inspected, and the user has import instructions."]
incompatible-with: []
priority: 15
---
# Create a pet

Use only for an explicit pet creation request. Create an original character. Preserve the user's files. Writes and commands use the runtime tool permission gates; this package grants no permissions.

1. Use the drawing contract below. If this workspace contains VOOL source, read `core/pet_cosmetics_creator.py` and `core/pet_cosmetics.py`; otherwise probe the installed module with `python -m core.pet_cosmetics_creator --help` through the permissioned command door. Do not read outside the permitted workspace. Use the character idea from the request. Do not copy named characters or impersonate a real voice.
2. In the user's workspace write a JSON drawing with exactly `id`, `name`, `author`, `license`, `description`, `palette`, `base`, `states`, `bindings`. Use author `sls_0x` for newly authored original art; retain imported authorship. Choose a licence appropriate to the user's own artwork.
3. `palette` maps short lowercase identifiers to `#RRGGBBAA`. `base` is at most 128 rectangles `[x,y,width,height,palette_id]` on a transparent 48 by 48 canvas. All rectangles must fit. Each state is one to eight frames; each frame is a list of up to 128 overlay rectangles. Give all eleven states distinct readable expressions: idle, starting, thinking, tool, waiting, approval, success, failure, cancelled, unknown, retry. Optional expressions: love, happy, surprise, spar, nap, work, wave. Big readable eyes and mouth help. Use no text in the artwork.
4. `bindings` maps event names to `{expression, sound}`. Initially set sound to an empty string. Unknown, failure and cancelled must use their corresponding expressions. Optional work categories are read, search, code, test, git and run. Never portray unverified work as success.
5. Through `sandbox.run_command`, run the installed interpreter with `-m core.pet_cosmetics_creator --workspace <workspace> --source <drawing.json> --output <pet.voolpet>`. Paths stay inside the workspace, and the output must not exist. The command validates the exported pack and writes a PNG contact sheet alongside it. Inspect the preview using available workspace image tools. Do not claim inspection if unavailable.
6. In Settings → Toybox → Pets and sounds, open the `.voolpet`, preview reactions, import, then select it. Assign a reaction and a built-in or imported voice for each event. Sounds are off until enabled. User WAV or MP3 clips can be imported there; they must be short (five seconds maximum). Packs accept normalized mono 24 kHz PCM WAV, at most sixteen clips. The interface performs conversion. No external speech service or key is needed.
7. Report the pack and preview paths, actual validation outcome, and remaining visual limitations. A pack has front/left/right slots; this rectangle workflow gives each direction the same poses. For directional art, edit the atlas and frame maps as inert data and revalidate with the same importer. Never claim unique direction art when the frames repeat.

Packs are data, never scripts. Do not run code embedded in an import, fetch media from URLs, overwrite prior work, spend money, or change growth records. The web chat and Yard support custom pets; the detached desktop pet retains its own roster.
