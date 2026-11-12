---
name: vool-create-world
description: "Build an original playable world from a user idea, using the shared Yard units and validated world files. Use for an explicit request to create a VOOL world, park, course or gathering place."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
id: create-world
risk-class: workspace_write
task-families: []
triggers: [create a world, build a world, make a world, new world, world pack, playable world, build a park, build a course, gathering place]
capability-families: [workspace]
tool-intents: [workspace.read_file, workspace.write_file, sandbox.run_command]
permitted-tools: [workspace.read_file, workspace.write_file, workspace.ensure_directory, sandbox.run_command]
prerequisites: []
expected-outputs: [world_pack, layout_preview, import_instructions, verification_statement]
verification: [typed_receipts, cumulative_suite_green]
stopping-conditions: ["Stop after the world validates, its layout preview is inspected and the user has import instructions."]
incompatible-with: []
priority: 15
---
# Create a world

Create an original place from the user's request. Use existing blocks and activities; do not copy named worlds or put executable game code into a world file. Preserve existing work. Runtime tool permissions still apply.

1. Read `core/pet_world/standard.py`, `catalog.py`, `model.py` and `creator.py` when the workspace contains this source; otherwise probe `python -m core.pet_world.creator --help` through the permitted command door. Never read outside the workspace.
2. The shared standard is a 24 by 24 grid with at most eight blocks vertically. Tile projection is 32 by 16 pixels, with an eight-pixel block rise. Pet atlases use 48 by 48 cells at the standard display scale. Decoration footprints and rotations come from the catalog. These are logical units, not a request to give every object the same silhouette.
3. Write a JSON recipe with exactly `name`, `description`, `base`, `fills`, `decorations`, `pets`. Use a name of at most 24 printable characters and a description of at most 300. `base` is a stack of `[material,count]` runs. `fills` is at most 128 ordered rectangles, each exactly `{x,y,width,depth,stack}`. All coordinates fit the grid; every stack uses known materials and the height cap. Empty stacks make holes. Later fills replace earlier columns. There are no loops, scripts, URLs or arbitrary expressions.
4. Decorations are `{id,x,y,rotation}` with at most 96 items. Rotations are 0 to 3. Footprints must fit, avoid overlap and sit on level ground; bridges may cross water. Pets are at most four `{id,x,y}` entries on walkable cells. Use valid roster ids or an already authored content-addressed pet id. Personal lifeforms and work-growth records are never exported. Leave walkable routes, gathering space and room to build. A race uses a continuous, walkable track; fetch and free building use the existing activities. Do not claim that an arbitrary game rule was implemented.
5. Run `python -m core.pet_world.creator --workspace <workspace> --source <recipe.json> --output <world.voolworld>`. The output must not exist. The real model validates the complete result. A PNG layout map is written beside it; inspect it and describe its limits. Actual pet and decoration art is previewed in Pet Yard.
6. Open the world file in Pet Yard's Worlds section, inspect the actual preview, and explicitly choose to use the local copy. Required custom pet files must be imported first. Exported worlds are integrity-checked data snapshots, not authenticated claims of authorship.
7. Shared online rooms are not part of this VOOL version. If the user asks for multiplayer, say so plainly and offer the portable world file instead. Do not read unrelated credentials, expose a private profile, publish a world, charge money or create tokens.
8. Return the world and preview paths, actual validation result, current limits, and import instructions. Do not fabricate review, live creation, ownership or publication proof.
