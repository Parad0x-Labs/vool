"""Original vector drawings; no text or executable content in the artwork."""
from __future__ import annotations


def _svg(palette, body, width=640, height=240):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
            f"<rect width='{width}' height='{height}' fill='{palette['panel']}'/>" + body + "</svg>")


def _radio(p):
    rim, accent, ink = p["border"], p["accent"], p["ink"]
    screws = "".join(f"<circle cx='{x}' cy='{y}' r='5' fill='{rim}'/><path d='M{x-2} {y}h4' stroke='{p['background']}'/>"
                     for x, y in ((210, 28), (430, 28), (210, 214), (430, 214)))
    grill = "".join(f"<path d='M222 {y}h28' stroke='{rim}' stroke-width='3'/>" for y in range(72, 180, 9))
    bars = "".join(f"<rect x='{280+i*15}' y='{148-i*9}' width='9' height='{12+i*9}' fill='{accent}'/>" for i in range(8))
    body = (f"<path d='M410 20V2h7v18' stroke='{rim}' stroke-width='6'/>"
            f"<rect x='194' y='18' width='252' height='210' rx='28' fill='{p['field']}' stroke='{rim}' stroke-width='8'/>"
            f"<rect x='267' y='56' width='151' height='120' rx='12' fill='{p['background']}' stroke='{rim}' stroke-width='3'/>"
            + screws + grill + bars
            + f"<circle cx='282' cy='200' r='14' fill='{accent}'/><circle cx='398' cy='200' r='16' fill='{rim}'/>"
            f"<path d='M398 200l8 -8' stroke='{ink}' stroke-width='3'/><path d='M302 197h58' stroke='{rim}' stroke-width='6'/>")
    header = "".join(f"<rect x='{12+i*24}' y='18' width='12' height='{8+(i%5)*5}' fill='{accent}'/>" for i in range(26))
    return {"empty": _svg(p, body), "header": _svg(p, header, height=64)}


def _journal(p):
    edge, accent = p["border"], p["accent"]
    paper = (f"<path d='M82 44Q200 14 320 42Q440 14 558 44V210Q440 184 320 216Q202 184 82 210Z' fill='{p['field']}' stroke='{edge}' stroke-width='4'/>"
             f"<path d='M320 42V216' stroke='{edge}' stroke-width='5'/>")
    rules = "".join(f"<path d='M108 {y}Q196 {y-15} 294 {y}M344 {y}Q440 {y-15} 532 {y}' fill='none' stroke='{edge}' opacity='.3'/>" for y in range(74, 190, 20))
    compass = (f"<circle cx='204' cy='119' r='37' fill='{p['panel']}' stroke='{edge}' stroke-width='2'/>"
               f"<path d='M204 74L214 119L204 164L194 119Z' fill='{accent}'/><path d='M160 119H248' stroke='{edge}' stroke-width='2'/>"
               f"<path d='M452 30V116l-14 -10 -14 10V28' fill='{accent}'/><circle cx='457' cy='168' r='21' fill='{accent}'/>")
    header = (f"<path d='M0 50Q160 10 320 42Q480 10 640 50M320 0V64' fill='none' stroke='{edge}' stroke-width='4'/>"
              f"<path d='M548 0V58l-12 -8 -12 8V0' fill='{accent}'/>")
    return {"empty": _svg(p, paper + rules + compass), "header": _svg(p, header, height=64)}


def _observatory(p):
    edge, accent = p["border"], p["accent"]
    stars = "".join(f"<circle cx='{x}' cy='{y}' r='{r}' fill='{accent}'/>" for x, y, r in ((66, 38, 2), (148, 69, 3), (238, 27, 2), (322, 54, 3), (406, 25, 2), (493, 64, 3), (574, 35, 2)))
    dome = (f"<path d='M154 188A166 166 0 0 1 486 188Z' fill='{p['background']}' stroke='{edge}' stroke-width='5'/>"
            f"<path d='M320 25Q258 84 267 188M320 25Q382 84 373 188M160 151H480' fill='none' stroke='{edge}' stroke-width='3'/>"
            f"<path d='M263 137l89 -48 20 36 -89 48Z' fill='{accent}' stroke='{edge}' stroke-width='3'/>"
            f"<circle cx='366' cy='106' r='22' fill='{p['field']}' stroke='{edge}' stroke-width='5'/>"
            f"<path d='M312 155l-32 67M312 155l38 67M312 155v67' stroke='{edge}' stroke-width='6'/>"
            f"<rect x='130' y='218' width='380' height='10' rx='5' fill='{edge}'/>")
    header = stars + f"<path d='M66 38L148 69 238 27 322 54 406 25 493 64 574 35' stroke='{edge}' fill='none' stroke-width='1.5'/>"
    return {"empty": _svg(p, stars + dome), "header": _svg(p, header, height=80)}


def _cabinet(p):
    edge, accent = p["border"], p["accent"]
    shell = (f"<path d='M180 18H460l-12 142 30 56H162l30 -56Z' fill='{p['field']}' stroke='{edge}' stroke-width='7'/>"
             f"<rect x='204' y='58' width='232' height='106' rx='4' fill='{p['background']}' stroke='{edge}' stroke-width='5'/>"
             f"<path d='M198 34H442' stroke='{accent}' stroke-width='12'/><path d='M182 188H458' stroke='{edge}' stroke-width='4'/>"
             f"<path d='M230 196v-22' stroke='{edge}' stroke-width='7'/><circle cx='230' cy='170' r='11' fill='{accent}'/>"
             f"<ellipse cx='380' cy='195' rx='14' ry='8' fill='{accent}'/><ellipse cx='420' cy='195' rx='14' ry='8' fill='{edge}'/>")
    star = f"<path d='M320 76l8 16h18l-14 12 5 18 -17 -9 -17 9 5 -18 -14 -12h18Z' fill='{accent}'/>"
    pixels = "".join(f"<rect x='{x}' y='{y}' width='6' height='6' fill='{accent}' opacity='.5'/>" for x, y in ((232, 86), (252, 136), (396, 88), (380, 140), (264, 108), (408, 118)))
    header = "".join(f"<rect x='{x}' y='10' width='12' height='12' fill='{accent if i%2 else edge}'/><rect x='{x}' y='42' width='12' height='8' fill='{edge}'/>" for i, x in enumerate(range(8, 640, 24)))
    return {"empty": _svg(p, shell + star + pixels), "header": _svg(p, header, height=64)}


def artwork(identifier, palette):
    draw = {"pocket-transceiver": _radio, "field-journal": _journal,
            "night-observatory": _observatory, "midnight-cabinet": _cabinet}.get(identifier)
    if not draw:
        return {}
    result = draw(palette)
    edge, accent, field = palette['border'], palette['accent'], palette['field']
    shapes = {
        'pocket-transceiver': f"<rect x='6' y='5' width='148' height='12' rx='5' fill='{field}' stroke='{edge}' stroke-width='3'/><path d='M18 7h6m-3 -3v6M136 7h6m-3 -3v6' stroke='{accent}' stroke-width='2'/>",
        'field-journal': f"<path d='M4 5Q40 1 80 6Q120 1 156 5v12Q120 13 80 18Q40 13 4 17Z' fill='{field}' stroke='{edge}' stroke-width='2'/><path d='M80 6v12M22 5v23l7 -5 7 5V4' fill='{accent}' stroke='{edge}'/>",
        'night-observatory': f"<ellipse cx='80' cy='9' rx='76' ry='6' fill='{field}' stroke='{edge}' stroke-width='3'/><path d='M28 15v12m104 -12v12M60 8h40' stroke='{accent}' stroke-width='4'/>",
        'midnight-cabinet': f"<path d='M10 4h140l9 14H1Z' fill='{field}' stroke='{edge}' stroke-width='3'/><path d='M8 21h144' stroke='{edge}' stroke-width='4'/><circle cx='24' cy='10' r='3' fill='{accent}'/><path d='M130 9h12' stroke='{accent}' stroke-width='3'/>",
    }
    result['perch'] = "<svg xmlns='http://www.w3.org/2000/svg' width='160' height='32' viewBox='0 0 160 32'>"+shapes[identifier]+"</svg>"
    return result
