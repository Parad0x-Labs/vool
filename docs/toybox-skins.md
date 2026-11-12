# Toybox skins

Skins are local appearance files. Preview changes the open Settings window; Apply saves the
choice for chat and Settings. Restore VOOL default removes the saved appearance. Import never
permits scripts, CSS, network URLs or executable artwork.

## Engine v2

Contract `2.0` adds an optional `stage` object. Its closed vocabulary chooses a layout
(`standard`, `device`, `dock`, `spread`, `console`), controls (`standard`, `keys`, `dials`,
`glass`, `ink`), display type, frame, scene and reactive profile. Unknown fields are refused
at every depth. Contracts `1.0`, `1.1` and `1.2` retain their original output.

An optional `palettes` object contains validated `dark` and `light` palettes. Appearance's
Light, Dark and Same as this computer selection chooses the matching variant. Every text
colour must reach 4.5:1 on the background, panel and field; control borders need 3:1.

Assets are at most sixteen inert PNG objects, each with `path` and base64 `png` fields.
Names are closed `assets/name.png` identifiers, never disk paths. Images are decoded,
bounded to 2048 pixels per edge and four million pixels total, and re-encoded without
metadata. Frame top, side and corner slots have tighter 1024×64, 64×512 and 96×96 bounds;
empty-state artwork is at most 640×400. The entire JSON stays within 16 KiB.
Top artwork frames the header, side artwork frames the rail, and corner artwork frames the
composer footer independently. Empty-state images occupy a separate block above the greeting.
Scene choices have original static artwork even without a reactive profile. Selection and
caret choices use the validated palette. Forced-colour mode suppresses decorative artwork.

The display activity mirrors the visible chat's companion reducer. Only a passed review
can produce success. Unreviewed completion, unavailable presentation state and exceptions
mute the effects. The cadence contains timing only. No message or tool content is inspected.

Motion and layout preferences share the existing selection revision check. Reduced motion,
hidden pages, unfocused windows and approval or waiting states stop skin animation. Ambient
motion ends after five idle seconds. Pets keep their own motion controls. Protected approvals,
private messages and receipts retain the application's own palette.

## Settings

The gallery uses the full skin renderer in isolated, scriptless miniatures. A brief Preview
demonstration animates only its sandboxed frame. Apply and Save options use the same revision
check; Cancel makes no write. Motion and layout can be saved even while using the default look.
All controls use the UI language. Display roles use system font stacks; no fonts are fetched.

## Flagships A

Pocket Transceiver uses a rounded device shell, a signal readout, rubber keys and original
radio drawings. Field Journal uses a bound spread, ink controls, ribbon and compass drawings.
Night Observatory uses a brass instrument rail, dial controls and a star-chart dome.
Midnight Cabinet uses a framed screen and control deck, chunky keys and a pixel marquee.
Each has separate dark and light palettes. Their code-generated artwork contains no text.

V2 uses compact, explicitly guarded rules around trusted surfaces. The original reset remains
in force in the default appearance and every skin. Live reactive marks use one bounded Canvas at at most eight frames per second; scriptless previews use CSS.


## Flagships B

Tide Station has a pressure-hull console, portholes, kelp and reef drawings. Lantern Market
has brass framing, capsule controls, awnings and a string of paper lanterns. Orbital Bridge
uses a padded deck, viewport and planet limb. Glasshouse uses an iron and glass spread,
seedlings and a potting bench. All artwork is generated locally from each validated palette. The Activity panel and both resize handles keep their own grid areas; at intermediate widths Activity overlays below the header. Rail resizing keeps the stage width floor.
The Glasshouse's live vine is driven only by observed passed completions in this window;
revisiting the same completed chat adds nothing. The count is never saved.

The CPU reference uses the companion's supported reduced-motion setting with skin motion
still Full. Default runs surround each ten-second skin sample, with the same one-point
allowance. Normal companion motion is retained in the functional and streaming-frame matrix.
Its existing frame loop makes variable idle measurements on a shared machine; those raw
measurements are retained separately rather than presented as a reliable idle gate.

Large artwork variables belong only to the header and empty-state nodes. Chat retains only
selection data, and appearance refreshes on focus or visibility return. Gallery reload and
local mutations refresh directly; no background gallery polling runs in an idle chat.

## Dawn Circuit

Dawn Circuit has a stepped console, a pixel moon gate, ribbon clouds and two original
large-eyed mascots. Separate pastel and neon palettes follow Appearance. The bounded live
header faces mirror the same companion reducer as the other skins; unreviewed completion
stays unknown, and approval or reduced motion stops drawing. The original mascot grammar
also defines love, joy, surprise, sparring, working, waving and resting expressions.
Display text uses the installed monospace stack, with native fallbacks for other scripts;
the artwork itself is authored on a 48-square grid with crisp SVG edges.

## Pet Yard and personal companions

Open **Pet Yard** from the chat's Home menu or Settings → Toybox. The Yard is a built-in local page: the native skill loader carries instructions and tool contracts, and does not host application pages.

Choose up to four roster or imported pets, plus the home's own lifeform. Click a tile, use arrow keys, or use the on-screen direction buttons. Shift-drag pans the camera; touch dragging pans, and a tap chooses a destination. Zoom uses integer steps. In build modes, arrow keys move the tile cursor and Enter places the selected block or decoration. Erase removes decorations before blocks. Unsupported placements leave the current Yard intact. Undo and redo retain up to 100 edits. Save is explicit; reload warns about unsaved work. A stale save is refused rather than overwriting another window.

Click a pet for a reaction; a nearby pointer can draw it into a brief follow. Roster sprites use a 2× pixel scale, cached at their authored 48-pixel resolution. Selected skins theme the Yard chrome; the block materials retain their own colors.

The 24 by 24 Yard supports eight block levels, sixteen block materials and twenty original decorations. Everything is already free. Fetch follows a reachable path out and back. Race uses a connected, unbranched track of at least two tiles and a fixed seed; one participant is a time trial. Dig selects an existing finished decoration and opens its palette. Games never write work facts or growth events. Reduced motion removes autonomous movement and makes travel immediate. Hidden or unfocused pages stop their animation and polling loops.

**Pets and sounds** in Toybox imports a `.voolpet` or JSON pack after validation and an artwork/author preview. Importing does not select the pet or enable audio. Choose a built-in or imported companion and save to use it in web chat. Current companion follows the existing character picker; another Toybox choice overrides that picker in web chat. The same custom library supplies the Yard roster. The detached desktop pet keeps its existing roster.

Sounds start off. Enable them and save, then use a pointer or key gesture to unlock playback. Original short bright and deep adult voices are included in English, Japanese and Chinese. Each pet can assign a reaction and clip for a lifecycle state or a work category. A verified completion may celebrate; an unverified completion remains unknown. Unknown, failed and cancelled states cannot be assigned a success expression. Automatic voices play briefly, at most one at a time with a four-second cooldown. Hidden pages stop playback. There are no runtime speech-provider calls.

User WAV or MP3 clips must be at most five seconds and two channels; the browser decodes and resamples them to mono 24 kHz PCM WAV. The server independently validates, bounds and attenuates the samples. Failed imports keep prior choices. Media is served from the local runtime by its content hash. Damaged assets give a neutral fallback; they are never executed. Removing a pet still used by the saved Yard is refused. Remove it from the Yard, save, then remove it from the library.

### Create a pet

The native `create-pet` skill uses existing permissioned workspace and sandbox tools to draw original rectangle art, run the pack builder and provide a preview. It grants no additional permissions. The builder is also available directly:

```sh
python -m core.pet_cosmetics_creator --workspace . --source drawing.json --output moon-moth.voolpet
```

The source contains `id`, `name`, `author`, `license`, `description`, `palette`, `base`, `states`, and `bindings`. Palette values use `#RRGGBBAA`. Rectangles are `[x,y,width,height,palette_id]` inside a 48 by 48 canvas. `base` has up to 128 rectangles. Every state contains one to eight frames, each containing up to 128 overlay rectangles. The builder refuses paths outside the workspace and existing outputs. It writes an importable pack and PNG contact sheet, then validates the pack through the same importer. This workflow repeats its poses for front, left and right; directional artists can supply distinct atlas indices in a pack.

### Pack contract

A pack has exactly these top-level fields:

```json
{
  "schema": "vool.pet.v1", "version": 1,
  "id": "moon-moth", "name": "Moon Moth", "author": "sls_0x",
  "license": "CC0-1.0", "description": "An original moon moth",
  "atlas": {"cell": 48, "png": "base64 PNG bytes"},
  "frames": {}, "clips": {}, "bindings": {}
}
```

`frames` requires idle, starting, thinking, tool, waiting, approval, success, failure, cancelled, unknown and retry. Each maps to `{front:[indices],left:[indices],right:[indices]}` with one to eight indices per direction. Optional expressions are love, happy, surprise, spar, nap, work and wave. Indices address 48-pixel cells in row order. PNG dimensions must be cell multiples, at most 16 columns and 32 rows, and at most one million pixels. The PNG is decoded and rewritten without metadata.

Up to sixteen named `clips` contain `{name,wav}` with base64 mono 24 kHz PCM16 WAV. `bindings` maps lifecycle states, read/search/code/test/git/run, or the four emotes to `{expression,sound}`. In a pack, `sound` is a local clip identifier or an empty string. Toybox assignments additionally offer bundled voices and standalone imports. No URLs, scripts, CSS or executable files are supported. Pack JSON is bounded to four MiB, with the HTTP transport's four-MiB envelope still applying. The library holds up to sixteen pets, thirty-two standalone sounds and thirty-two MiB of media.

### Growth boundaries

The egg gate counts successful facts and distinct turn keys. Growth is checked in a foreground operation when the Yard opens, when Continue is chosen, or through `/lifeform`. Read-only appearance requests never feed growth. Facts before birth, failed/refused/cancelled facts, future timestamps and replayed facts give no credit. The existing kernel owns caps, event reduction, stages and signatures. The bridge verifies the chain and canonical stage before appending and never writes execution tables. A damaged record stays intact; a separately verified prior appearance is shown when available. Restoring an old valid local backup cannot be detected by this local scheme.

The Dawn Circuit display role embeds the unmodified Silkscreen Regular pixel font offline
([upstream source and OFL licence](https://github.com/google/fonts/tree/main/ofl/silkscreen)).
The licence text ships in `core/pet_world/licenses/silkscreen-OFL.txt` and is embedded alongside
the font bytes in `core/toybox_font.py`, so an installed package keeps it. CJK and other unsupported
glyphs fall back to the local system stack; a complete CJK bitmap font is not bundled.
