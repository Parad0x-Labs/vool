"""Original station, market, bridge and greenhouse drawings, without embedded text."""
from __future__ import annotations


def _svg(p, body, height=240):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='{height}' viewBox='0 0 640 {height}'>"
            f"<rect width='640' height='{height}' fill='{p['panel']}'/>" + body + "</svg>")


def _tide(p):
    edge, glow, water = p['border'], p['accent'], p['field']
    plants = ''.join(f"<path d='M{x} 208q-22 -28 0 -52q24 -26 0 -56m0 72q-22 -20 -28 -8m28 -24q24 -22 32 -8' fill='none' stroke='{glow}' stroke-width='5'/>" for x in (250, 285, 390))
    reef = (f"<circle cx='320' cy='122' r='103' fill='{water}' stroke='{edge}' stroke-width='10'/>"
            f"<circle cx='320' cy='122' r='91' fill='none' stroke='{edge}' stroke-width='2'/>" + plants +
            f"<path d='M300 96q22 -20 46 0q-24 20 -46 0m46 0l14 -10v20Z' fill='{glow}'/>"
            f"<path d='M234 200q80 -22 171 0' fill='none' stroke='{edge}' stroke-width='6'/>"
            f"<circle cx='366' cy='64' r='6' fill='none' stroke='{glow}' stroke-width='2'/><circle cx='356' cy='46' r='3' fill='{glow}'/>")
    header = ''.join(f"<circle cx='{x}' cy='32' r='27' fill='{water}' stroke='{edge}' stroke-width='5'/><path d='M{x-10} 50q-8 -14 2 -24m8 24q10 -20 0 -34' fill='none' stroke='{glow}' stroke-width='2'/><circle cx='{x+10}' cy='21' r='3' fill='{glow}'/>" for x in (80, 240, 400, 560))
    return {'empty': _svg(p, reef), 'header': _svg(p, header, 64)}


def _lantern(p, x, y, size=24):
    return (f"<path d='M{x} {y-12}v12m0 {size}v10' stroke='{p['border']}' stroke-width='2'/>"
            f"<rect x='{x-size/2}' y='{y}' width='{size}' height='{size}' rx='{size/3}' fill='{p['accent']}' stroke='{p['border']}' stroke-width='2'/>"
            f"<path d='M{x-size/3} {y+4}h{size*2/3}m-{size*2/3} {size-8}h{size*2/3}M{x} {y+2}v{size-4}' stroke='{p['panel']}' stroke-width='1.5'/>")


def _market(p):
    edge, cloth = p['border'], p['accent']
    stall = (f"<path d='M150 88H490l34 46H116Z' fill='{cloth}' stroke='{edge}' stroke-width='5'/>"
             f"<path d='M160 134v88m320 -88v88M160 180H480' stroke='{edge}' stroke-width='7'/>"
             f"<rect x='172' y='185' width='296' height='32' rx='3' fill='{p['field']}' stroke='{edge}' stroke-width='3'/>"
             f"<path d='M212 88l-8 46m62 -46l-4 46m58 -46v46m54 -46l4 46m50 -46l8 46' stroke='{p['panel']}' stroke-width='6'/>"
             f"<path d='M76 12Q320 76 564 12' stroke='{edge}' stroke-width='3' fill='none'/>")
    lamps = ''.join(_lantern(p, x, y) for x, y in ((112, 32), (216, 48), (424, 48), (528, 32)))
    header = f"<path d='M0 4Q320 58 640 4' fill='none' stroke='{edge}' stroke-width='2'/>" + ''.join(_lantern(p, x, 18+(i%3)*4, 25) for i, x in enumerate(range(40, 640, 80)))
    return {'empty': _svg(p, stall + lamps), 'header': _svg(p, header, 64)}


def _bridge(p):
    edge, space, planet = p['border'], p['background'], p['accent']
    viewport = (f"<path d='M96 30H544l38 160H58Z' fill='{space}' stroke='{edge}' stroke-width='7'/>"
                f"<path d='M236 184a118 118 0 0 1 236 0' fill='{planet}'/>"
                f"<path d='M271 184q30 -78 67 -94m44 94q-24 -64 -44 -94' fill='none' stroke='{p['field']}' stroke-width='8'/>"
                f"<path d='M74 194H566l-38 30H112Z' fill='{p['field']}' stroke='{edge}' stroke-width='5'/>")
    stars = ''.join(f"<path d='M{x-3} {y}h6m-3 -3v6' stroke='{planet}' stroke-width='1.5'/>" for x, y in ((138, 64), (202, 110), (431, 54), (504, 110)))
    controls = ''.join(f"<rect x='{x}' y='203' width='24' height='7' rx='2' fill='{planet}'/>" for x in range(162, 490, 56))
    header = (f"<rect x='5' y='3' width='630' height='58' rx='22' fill='{space}' stroke='{edge}' stroke-width='4'/>"
              f"<path d='M408 61a74 74 0 0 1 148 0' fill='{planet}'/><path d='M460 7q-14 22 -7 54m30 -61q14 27 8 61' fill='none' stroke='{p['field']}' stroke-width='5'/>"
              f"<path d='M42 22h6m-3 -3v6M164 38h6m-3 -3v6M285 18h6m-3 -3v6' stroke='{planet}' stroke-width='2'/>")
    return {'empty': _svg(p, viewport + stars + controls), 'header': _svg(p, header, 64)}


def _glasshouse(p):
    edge, leaf = p['border'], p['accent']
    shell = (f"<path d='M144 220V82Q320 -26 496 82V220Z' fill='{p['field']}' stroke='{edge}' stroke-width='5'/>"
             f"<path d='M320 28V220M232 44V220M408 44V220M144 110H496M144 160H496' fill='none' stroke='{edge}' stroke-width='3'/>"
             f"<path d='M165 190H475m-280 0v34m250 -34v34' stroke='{edge}' stroke-width='8'/>")
    plants = ''.join(f"<path d='M{x-10} 164h20l-3 20h-14Z' fill='{edge}'/><path d='M{x} 168v-32m0 13q-20 -18 -23 -4q8 13 23 9m0 -11q21 -20 24 -5q-8 14 -24 12' fill='{leaf}' stroke='{leaf}' stroke-width='2'/>" for x in (198, 258, 320, 382, 442))
    header = (f"<path d='M0 62V8Q320 -6 640 8V62M0 32H640' fill='none' stroke='{edge}' stroke-width='3'/>"
              + ''.join(f"<path d='M{x} 4v58' stroke='{edge}' stroke-width='2'/>" for x in range(80, 640, 80))
              + ''.join(f"<path d='M{x} 60V14m0 12q-22 -20 -27 -5q8 14 27 12m0 9q22 -22 28 -7q-8 14 -28 13' fill='{leaf}' stroke='{leaf}' stroke-width='2'/>" for x in (36, 604)))
    return {'empty': _svg(p, shell + plants), 'header': _svg(p, header, 64)}


def artwork(identifier, palette):
    draw = {'tide-station': _tide, 'lantern-market': _market,
            'orbital-bridge': _bridge, 'glasshouse': _glasshouse}.get(identifier)
    if not draw:
        return {}
    result = draw(palette)
    edge, accent, field = palette['border'], palette['accent'], palette['field']
    shapes = {
        'tide-station': f"<path d='M2 5Q80 15 158 5v12Q80 29 2 17Z' fill='{field}' stroke='{edge}' stroke-width='3'/><circle cx='14' cy='12' r='3' fill='{accent}'/><circle cx='146' cy='12' r='3' fill='{accent}'/>",
        'lantern-market': f"<path d='M4 5h152v12q-8 9 -16 0q-8 9 -16 0q-8 9 -16 0q-8 9 -16 0q-8 9 -16 0q-8 9 -16 0q-8 9 -16 0q-8 9 -16 0q-8 9 -24 0Z' fill='{field}' stroke='{edge}' stroke-width='2'/><path d='M28 6v10m24 -10v10m24 -10v10m24 -10v10m24 -10v10' stroke='{accent}' stroke-width='6'/>",
        'orbital-bridge': f"<rect x='3' y='4' width='154' height='11' rx='5' fill='{field}' stroke='{edge}' stroke-width='3'/><path d='M24 16v12m112 -12v12M36 8h16m56 0h16' stroke='{accent}' stroke-width='4'/>",
        'glasshouse': f"<path d='M5 10q40 -8 75 0t75 0' fill='none' stroke='{edge}' stroke-width='8'/><path d='M22 10q-18 -13 -18 -5q5 10 18 5m-1 1q-9 18 2 13q8 -3 -2 -13m116 0q18 -15 18 -5q-5 10 -18 5m1 0q12 15 0 13q-9 -3 0 -13' fill='{accent}'/>",
    }
    result['perch'] = "<svg xmlns='http://www.w3.org/2000/svg' width='160' height='32' viewBox='0 0 160 32'>"+shapes[identifier]+"</svg>"
    return result
