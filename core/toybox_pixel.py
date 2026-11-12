"""Original pixel mascots and a moon-gate console, shared with the local yard."""
from __future__ import annotations

from core.i18n.catalog import load_catalog_data
from core.pet_world.anime_art import EXPRESSIONS as EXPRESSIONS
from core.pet_world.anime_art import MASCOTS as MASCOTS
from core.pet_world.anime_art import mascot_pixels as mascot_pixels
from core.pet_world.anime_art import mascot_svg
from core.toybox import _example


def artwork(identifier, p):
    if identifier != 'dawn-circuit':
        return {}
    edge, accent = p['border'], p['accent']
    stars = ''.join(f"<path d='M{x} {y}h4v4h4v4h-4v4h-4v-4h-4v-4h4Z' fill='{accent}'/>" for x, y in ((36, 20), (116, 8), (196, 24), (436, 12), (516, 26), (596, 8)))
    ribbon = f"<path d='M0 42h72v-8h80v8h80v-8h80v8h80v-8h80v8h80v-8h88' fill='none' stroke='{edge}' stroke-width='4'/>"
    moon = f"<path d='M252 36h136v8h16v16h8v128h-16V68h-8V60H252v8h-8v120h-16V60h8V44h16Z' fill='{edge}'/><path d='M260 68h120v112H260Z' fill='{p['field']}'/><path d='M324 80h24v8h8v16h-8v8h-24v-8h-8V88h8Z' fill='{accent}'/><path d='M332 80h24v24h-16V96h-8Z' fill='{p['field']}'/>"
    ground = f"<path d='M56 212h528v8H56Z' fill='{edge}'/><path d='M104 220h432v8H104Z' fill='{p['field']}'/>"
    clouds = ''.join(f"<path d='M{x} {y}h16v-8h24v8h16v8h-56Z' fill='{accent}' opacity='.55'/>" for x, y in ((62, 40), (504, 60)))
    creatures = "<g transform='translate(110 74) scale(3)'>" + mascot_svg('lumen-fox', 'happy', p) + "</g><g transform='translate(390 74) scale(3)'>" + mascot_svg('dusk-lynx', 'surprise', p) + '</g>'
    def svg(body, height):
        return f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='{height}' shape-rendering='crispEdges'>{body}</svg>"
    perch = (f"<svg xmlns='http://www.w3.org/2000/svg' width='160' height='32' shape-rendering='crispEdges'><path d='M8 4h144v4h8v12H0V8h8Z' fill='{edge}'/><path d='M12 8h136v6H12Z' fill='{p['field']}'/><path d='M4 24h12v4H4m140 -4h12v4h-12M24 4h8v8h-8m104 -8h8v8h-8' fill='{accent}'/></svg>")
    return {'header': svg(stars+ribbon, 64), 'empty': svg(moon+ground+clouds+creatures, 240), 'perch': perch}


def _palette(light):
    return dict(background='#f6effb' if light else '#211a38', panel='#fffaff' if light else '#2c2546',
                field='#ebdff3' if light else '#251e3d', ink='#242036' if light else '#fff4fd',
                muted='#59445e' if light else '#d6bddf', accent='#69315e' if light else '#ffb8d6',
                accentInk='#ffffff' if light else '#291528', border='#896390' if light else '#b9a0de')


_strings = load_catalog_data('en')['messages']
_skin = _example('dawn-circuit', _strings['toybox.skin_dawn-circuit_name'],
                 _strings['toybox.skin_dawn-circuit_description'], _palette(False),
                 'flat', 'none', 6, 2, 'system', '2.0', '1.0.0')
_skin['palettes'] = {'dark': _palette(False), 'light': _palette(True)}
_skin['stage'] = {'layout': 'console', 'controls': 'keys', 'type': 'pixel-display',
                  'frame': {'set': 'cabinet'}, 'scene': {'id': 'stars', 'density': 'calm'},
                  'reactive': {'profile': 'glow', 'accentFrom': 'accent'}}
SKINS = (_skin,)
