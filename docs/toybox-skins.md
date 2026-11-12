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

The Dawn Circuit display role embeds the unmodified Silkscreen Regular pixel font offline
([upstream source and OFL licence](https://github.com/google/fonts/tree/main/ofl/silkscreen)).
The licence text is embedded alongside the font bytes in `core/toybox_font.py`, so an installed
package keeps it. CJK and other unsupported glyphs fall back to the local system stack.
