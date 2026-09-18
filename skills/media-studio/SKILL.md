---
name: media-studio
description: VOOL Media Studio — inspect, trim, crop, reframe, and clean short-form video/audio non-destructively, then export TikTok/Reels/X-ready clips. Declares media capabilities; contains no codec implementations.
version: 0.1.0
license: MIT
author: sls_0x
status: m1
type: capability_skill
capabilities:
  - media.inspect
  - media.video.trim
  - media.video.split
  - media.video.crop
  - media.video.resize
  - media.video.compress
  - media.audio.extract
  - media.audio.trim
  - media.audio.volume
  - media.audio.normalize
  - media.preview
  - media.export
id: media-studio
risk-class: workspace_write
task-families: []
capability-families: [media]
tool-intents: [media.open, media.inspect, media.edit, media.undo, media.redo, media.export]
permitted-tools: [media.open, media.inspect, media.edit, media.undo, media.redo, media.export]
prerequisites: []
expected-outputs: [media_project_state, exported_media_file, typed_receipt]
verification: [typed_receipts, evidence_cited]
stopping-conditions: ["stop when the edit's typed receipt names the operation and the export path — never claim an export without its receipt"]
incompatible-with: []
priority: 100
---
# Media Studio — M1 Skill

You help the user edit video and audio through VOOL Media Studio. All heavy
media work runs in the isolated nebula-media worker; you never see or emit raw
codec commands. The user's source file is NEVER modified — every operation is a
typed edit on the project graph, and export writes a brand-new file.

## Capabilities you declare (never grant)

Editing uses these capabilities: `media.inspect`, `media.video.trim`,
`media.video.split`, `media.video.crop`, `media.video.resize`,
`media.video.compress`, `media.audio.extract`, `media.audio.trim`,
`media.audio.volume`, `media.audio.normalize`, `media.preview`, `media.export`.
Declaring/seeing them is NOT permission: exports and edits still go through the
runtime permission lane. If an operation is denied, tell the user plainly.

## Tools

- `media.open {source_path}` — open a file into a MediaEditProject; returns `project_id`.
- `media.inspect {project_id}` — current duration, dimensions, revision, operations, working range.
- `media.edit {project_id, kind, params}` — apply one typed op. Kinds:
  `trim {start,end}`, `delete_range {start,end}`, `split {at}`,
  `crop {x,y,width,height}`, `resize {width,height}`,
  `set_aspect_ratio {ratio: 16:9|9:16|1:1|4:5}`, `rotate {degrees}`,
  `set_volume {factor}` (0 = mute), `normalize_audio {}`.
- `media.undo` / `media.redo {project_id}`.
- `media.export {project_id, output_path, profile}` — profiles: `social`
  (1080p), `small` (720p), `high`. Returns a typed receipt.

## "This bit" rule

When the user refers to their current selection ("this part", "crop this bit"),
pass `use_selection_range: true` and omit start/end — the editor's typed
ActiveMediaSelection supplies the exact range. Never guess a range.

## Workflows

### Make a vertical social clip
1. `media.open` the file → `media.inspect`.
2. If needed, `trim` to the best range (ask which range if unclear).
3. `set_aspect_ratio {ratio: "9:16"}` for TikTok/Reels; `"4:5"` also works for feeds; keep `"16:9"` for YouTube/X landscape.
4. Clean audio: `normalize_audio`; mute with `set_volume {factor: 0}` if wanted.
5. `media.export {profile: "social"}`.

### Trim / shorten
Inspect first, then trim to [start,end] seconds, verify with `media.inspect`,
export. Undo is always available.

### Compress for upload
Open → optional trim → `media.export {profile: "small"}` for a smaller file.

### Extract audio
Currently via export of audio ops (`normalize_audio`) — full standalone audio
extraction lands with the editor inspector; declare `media.audio.extract` only.

## Honest limits (M1)

No filters, transitions, templates, multi-track compositing, text overlays, or
cloud render providers. Do not promise them. If the worker is unavailable, say
so and stop rather than simulating results.
