"""Bounded stage vocabulary, inert PNG slots and presentation-only motion."""
from __future__ import annotations

import base64
import re
from io import BytesIO

LAYOUTS = ("standard", "device", "dock", "spread", "console")
CONTROLS = ("standard", "keys", "dials", "glass", "ink")
TYPES = ("system", "rounded-display", "serif-display", "mono-readout", "pixel-display")
FRAMES = ("bezel", "cabinet", "book", "porthole", "brass", "none")
SCENES = ("none", "sky", "stars", "sea", "lanterns", "glasshouse", "grid")
PROFILES = ("none", "meter", "glow", "sweep", "bloom")
SLOTS = {"top": (1024, 64), "side": (64, 512), "corner": (96, 96), "empty": (640, 400)}
DEFAULT = {
    "layout": "standard", "controls": "standard", "type": "system",
    "frame": {"set": "none", "art": {}},
    "scene": {"id": "none", "density": "calm"},
    "reactive": {"profile": "none", "accentFrom": "accent"},
    "empty": {}, "accents": {"selection": "accent", "caret": "ink"},
}


def governor_css():
    from core.toybox_fragment import GUARD, ISLAND_RESET

    subject = ':root[data-vool-motion="off"] :is(#main,#sidebar,#side,#pane,.tb-root) *' + GUARD
    return ISLAND_RESET + "".join(subject + pseudo + "{animation:none!important;transition:none!important}\n" for pseudo in ("", "::before", "::after"))


def validate_stage(value, assets, require):
    def fields(obj, allowed, path):
        require(type(obj) is dict and not set(obj) - set(allowed), path + ": unsupported fields.")

    def enum(obj, key, choices, path):
        if key in obj:
            require(type(obj[key]) is str and obj[key] in choices, path + "." + key + ": unsupported choice.")

    fields(value, DEFAULT, "stage")
    for key, choices in (("layout", LAYOUTS), ("controls", CONTROLS), ("type", TYPES)):
        enum(value, key, choices, "stage")
    groups = (("frame", {"set": FRAMES, "art": None}),
              ("scene", {"id": SCENES, "density": ("calm", "lively")}),
              ("reactive", {"profile": PROFILES, "accentFrom": ("accent",)}),
              ("empty", {"art": None}),
              ("accents", {"selection": ("accent", "panel"), "caret": ("accent", "ink")}))
    for group, schema in groups:
        if group not in value:
            continue
        fields(value[group], schema, "stage." + group)
        for key, choices in schema.items():
            if choices:
                enum(value[group], key, choices, "stage." + group)
    art = value.get("frame", {}).get("art", {})
    fields(art, ("top", "side", "corner"), "stage.frame.art")
    refs = {**art}
    if "art" in value.get("empty", {}):
        refs["empty"] = value["empty"]["art"]
    require(type(assets) is list and len(assets) <= 16, "assets: at most 16 PNGs.")
    images, pixels = {}, 0
    for item in assets:
        fields(item, ("path", "png"), "assets")
        require(set(item) == {"path", "png"}, "assets: path and png required.")
        path, data = item["path"], item["png"]
        require(type(path) is str and re.fullmatch(r"assets/[a-z0-9-]{1,64}\.png", path), "assets.path: invalid PNG name.")
        require(path not in images, "assets.path: duplicate PNG.")
        require(type(data) is str and len(data) <= 16000, "assets.png: oversized PNG.")
        try:
            from PIL import Image

            raw = base64.b64decode(data, validate=True)
            require(raw.startswith(b"\x89PNG\r\n\x1a\n"), "assets.png: PNG required.")
            with Image.open(BytesIO(raw)) as image:
                width, height = image.size
                require(0 < width <= 2048 and 0 < height <= 2048, "assets.png: dimensions exceed 2048.")
                pixels += width * height
                require(pixels <= 4_000_000, "assets.png: pixel budget exceeded.")
                image.load()
                output = BytesIO()
                image.convert("RGBA").save(output, format="PNG")
                item["png"] = base64.b64encode(output.getvalue()).decode("ascii")
                images[path] = (width, height)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            require(False, "assets.png: invalid PNG: " + type(exc).__name__)
    for slot, path in refs.items():
        require(type(path) is str and path in images, "stage." + slot + ": missing PNG.")
        width, height = images[path]
        maximum = SLOTS[slot]
        require(width <= maximum[0] and height <= maximum[1], "stage." + slot + ": PNG exceeds slot dimensions.")


def spec(manifest):
    stage = manifest.get("stage", {})
    return {key: {**default, **stage.get(key, {})} if isinstance(default, dict)
            else stage.get(key, default) for key, default in DEFAULT.items()}


def scene_svg(identifier, p, lively=False):
    """Static preview counterpart of the bounded live scene; always application-owned."""
    edge, accent = p["border"], p["accent"]
    count = 12 if lively else 6
    body = ""
    if identifier == "stars":
        body = "".join(f"<path d='M{x-3} {y}h6m-3 -3v6' stroke='{accent}' stroke-width='1.5'/>" for x, y in ((20+i*600//count, 8+(i*13)%26) for i in range(count)))
    elif identifier == "sky":
        body = "".join(f"<path d='M{x} 30q-12 -12 6 -14q8 -18 24 -4q22 -4 22 12h-52' fill='{accent}' opacity='.5'/>" for x in range(30, 640, 140))
    elif identifier == "sea":
        body = "".join(f"<path d='M{x} 42q-16 -16 0 -28m0 20q16 -12 24 -8' fill='none' stroke='{edge}' stroke-width='3'/><circle cx='{x+52}' cy='12' r='4' fill='none' stroke='{accent}' stroke-width='2'/>" for x in range(24, 640, 100))
    elif identifier == "lanterns":
        body = f"<path d='M0 4Q320 40 640 4' fill='none' stroke='{edge}' stroke-width='2'/>" + "".join(f"<path d='M{x} 8v8m0 20v6' stroke='{edge}' stroke-width='2'/><rect x='{x-9}' y='16' width='18' height='20' rx='6' fill='{accent}'/>" for x in range(40, 640, 80))
    elif identifier == "glasshouse":
        body = "".join(f"<path d='M{x} 0v44m0 -22h80' stroke='{edge}' stroke-width='2'/><path d='M{x+16} 40v-22m0 10q-14 -16 -18 -5q6 10 18 7m0 -4q14 -16 18 -5q-6 10 -18 7' fill='{accent}'/>" for x in range(0, 640, 80))
    elif identifier == "grid":
        body = "".join(f"<path d='M{x} 0v44' stroke='{accent}' opacity='.35'/>" for x in range(0, 640, 24)) + "".join(f"<path d='M0 {y}h640' stroke='{edge}'/>" for y in range(4, 44, 12))
    if not body:
        return None
    return f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='44'><rect width='640' height='44' fill='{p['panel']}'/>{body}</svg>"


def css(manifest, has_art=False):
    from core.toybox_fragment import _pinned, _rule

    stage = spec(manifest)
    layout, frame = stage["layout"], stage["frame"]["set"]
    rail = {"standard": 248, "device": 188, "dock": 184, "spread": 264, "console": 208}[layout]
    out = f":root{{--tb-layout:{layout};--tb-controls:{stage['controls']};--tb-profile:{stage['reactive']['profile']};--tb-scene:{stage['scene']['id']};--tb-density:{stage['scene']['density']}}}\n"
    prefix = "html:not([data-vool-layout=standard])"
    closed_rail = f"min(var(--tb-user-rail,{rail}px),calc(100vw - 645px))"
    open_rail = f"min(var(--tb-user-rail,{rail}px),calc(100vw - 900px))"
    out += f"@media(min-width:1000px){{{prefix} body[data-vool-stage=chat]{{display:grid;grid-template-columns:minmax(180px,{closed_rail}) 5px minmax(640px,1fr);grid-template-areas:'rail rail-grip stage'}}{prefix} #sidebar{{grid-area:rail;width:auto!important;min-width:0}}{prefix} #sideResize{{grid-area:rail-grip;width:5px}}{prefix} #main{{grid-area:stage;min-width:0}}}}\n"
    out += f"@media(min-width:1200px){{{prefix} body[data-vool-stage=chat].panel-open{{grid-template-columns:minmax(180px,{open_rail}) 5px minmax(640px,1fr) 5px max-content;grid-template-areas:'rail rail-grip stage pane-grip pane'}}{prefix} #xpanel{{grid-area:pane;min-width:0;max-width:calc(100vw - {open_rail} - 650px)}}{prefix} #xpResize{{grid-area:pane-grip;width:5px}}}}\n"
    out += f"@media(min-width:1000px) and (max-width:1199px){{{prefix} #xpanel{{position:fixed;left:auto;right:auto;inset-inline-end:0;top:var(--app-header-h,54px);bottom:0;z-index:25;max-width:calc(100vw - 56px);box-shadow:0 4px 12px #0008}}{prefix} #xpResize{{position:fixed;left:auto!important;right:auto!important;inset-inline-end:var(--tb-pane-width,340px);top:var(--app-header-h,54px);bottom:0;width:5px;z-index:26}}}}\n"
    # Logical borders preserve the first-paint direction and the DOM's tab order.
    rims = {
        "none": "", "bezel": "border:6px solid var(--border);border-radius:26px;box-shadow:inset 0 0 0 3px var(--field)",
        "cabinet": "border:8px ridge var(--border);border-radius:8px 8px 22px 22px",
        "book": "border:5px solid var(--border);border-inline-start-width:14px;border-radius:8px 20px 20px 8px",
        "porthole": "border:7px double var(--border);border-radius:32px",
        "brass": "border:6px double var(--border);border-radius:18px 18px 4px 4px",
    }
    band = has_art or frame != "none" or stage["scene"]["id"] != "none" or stage["reactive"]["profile"] != "none"
    out += _rule(("#main > header",), f"position:relative;background:var(--panel);padding-block-start:{50 if band else 12}px;" + rims[frame])
    out += _rule(("#main > header > .brand",), "background:var(--panel);border-radius:6px;padding:3px 6px")
    out += _rule(("#main > header",), "background-image:var(--tb-stage-header,none);background-size:auto 44px;background-repeat:repeat-x;background-position:center top")
    out += _rule(("#sidebar", "#side"), "border-inline-end:4px double var(--border);background:var(--panel)")
    out += _rule(("#sidebar .side-head",), "flex-wrap:wrap;align-content:start;min-width:0")
    out += _rule(("#sidebar .side-title",), "flex-basis:100%")
    out += _rule(("#main > footer",), "background:var(--bg)")
    if frame == "book":
        out += _rule(("#sidebar", "#side"), "box-shadow:inset -10px 0 18px var(--tb-lo-soft);border-inline-end:12px double var(--border)")
    if layout == "console":
        out += _rule(("#main > footer",), "border:6px ridge var(--border);border-radius:18px 18px 0 0;background:var(--panel)")
    if layout == "device":
        out += _rule(("#main > footer",), "border:5px solid var(--border);border-radius:0 0 26px 26px;background:var(--panel)")
    if layout == "device":
        out += f"@media(min-width:1000px){{{prefix} #main{{max-width:920px;width:100%;justify-self:center}}}}\n"
    if layout == "dock":
        from core.toybox_chrome import DOCK_CSS
        out += DOCK_CSS.replace("__P__", prefix)
    out += _rule(("#main", "#log", ".msg-text", "#pane", ".set-section", "#main > header > *"), "background-color:var(--bg);color:var(--ink)")
    out += _rule(("#main > header",), "--tb-pulse:0")
    profile = stage["reactive"]["profile"]
    if profile != "none":
        out += _rule(("#main > header::after",), 'content:"";position:absolute;inset-block-start:14px;inset-inline-end:24px;width:104px;height:16px;pointer-events:none;opacity:.6;background:var(--accent);transform-origin:bottom;will-change:transform,opacity')
        if profile == "meter":
            out += _rule(("#main > header::after",), "background:repeating-linear-gradient(90deg,var(--accent) 0 7px,transparent 7px 12px);transform:scaleY(calc(.2 + var(--vool-cadence,0)*.8))")
        elif profile == "sweep":
            out += _rule(("#main > header::after",), "width:22px;height:22px;border:2px solid var(--accent);border-radius:50%;background:conic-gradient(from 0deg,transparent,var(--accent));inset-block-start:8px")
        elif profile == "bloom":
            out += _rule(("#main > header::after",), "border-radius:50%;width:18px;height:18px;box-shadow:-28px 0 0 var(--accent),-56px 0 0 var(--accent)")
    for slot, path in stage["frame"]["art"].items():
        image = next(item["png"] for item in manifest["assets"] if item["path"] == path)
        selectors = {"top": ("#main > header",), "side": ("#sidebar", "#side"), "corner": ("#main > footer",)}[slot]
        out += _rule(selectors, f"border-style:solid;border-width:8px;border-image-source:url(data:image/png;base64,{image});border-image-slice:8;border-image-width:8px;border-image-repeat:stretch")
    if stage["empty"].get("art"):
        image = next(item["png"] for item in manifest["assets"] if item["path"] == stage["empty"]["art"])
        out += _rule((".empty::before",), f'content:"";display:block;width:100%;height:190px;pointer-events:none;margin-block-end:12px;background:url(data:image/png;base64,{image}) center/contain no-repeat')
    out += f"@media(max-width:999px){{#main>header{{border-width:3px;padding-block-start:{44 if band else 12}px}}#main{{width:100%;min-width:0}}}}\n"
    out += "@media(forced-colors:active){#main>header,#main>footer,#sidebar,#side{background-image:none!important;border-image:none!important}#main>header::after,.empty::before,#tb-effect,#tb-perch{display:none!important}}\n"
    out += ':root[data-vool-motion="off"] #main>header::after{animation:none;opacity:.3}:root:is([data-vool-activity="failure"],[data-vool-activity="unknown"],[data-vool-activity="approval"],[data-vool-activity="waiting"]) #main>header::after{background:var(--muted);animation:none;opacity:.25}\n'
    out += '@media(prefers-reduced-motion:reduce){#main>header::after{animation:none!important}}\n'
    from core.toybox_controls import css as controls_css
    from core.toybox_type import css as type_css

    out += controls_css(stage["controls"]) + type_css(stage["type"])
    selection = stage["accents"]["selection"]
    selection_ink = "tb-accent-ink" if selection == "accent" else "ink"
    out += _rule(tuple(s + "::selection" for s in ("#main *", "#pane *", "#sidebar *", "#side *", "#xpanel *")), f"background:var(--{selection});color:var(--{selection_ink})")
    out += _pinned()
    return out



def reactive_css(manifest):
    """Animate only the presentation element, never application content."""
    from core.toybox_fragment import GUARD

    profile = spec(manifest)["reactive"]["profile"]
    if profile == "none":
        return ""
    animation = {"meter": "tb-signal", "glow": "tb-signal", "sweep": "tb-orbit", "bloom": "tb-signal"}[profile]
    target = "#main > header" + GUARD + "::after"
    active = ':root:is([data-vool-motion="full"],[data-vool-motion="calm"]):is([data-vool-activity="thinking"],[data-vool-activity="tool"],[data-vool-activity="streaming"]) '
    out = active + target + "{animation:" + animation + " 2s ease-in-out infinite}\n"
    out += ':root[data-vool-motion="full"][data-vool-ambient="on"][data-vool-activity="idle"] ' + target + "{animation:tb-signal 4s ease-in-out infinite}\n"
    out += ':root:not([data-vool-motion="off"])[data-vool-activity="success"] ' + target + "{animation:tb-arrival .8s ease-out 1}\n"
    out += "@keyframes tb-signal{50%{opacity:.25}}@keyframes tb-orbit{to{transform:rotate(360deg)}}@keyframes tb-arrival{from{opacity:.2;transform:scale(.5)}to{opacity:.7;transform:scale(1)}}\n"
    out += ':root[data-tb-canvas] ' + target + '{animation:none!important;content:none}\n'
    return out


def render(manifest):
    import importlib

    from core.toybox_fragment import ALIASES, GUARD, ISLAND_RESET, _rule, _tokens
    from core.toybox_materials import css_url
    from core.toybox_registry_v2 import BUILTIN_V2_MODULES

    def drawings(palette):
        result = {}
        for name in BUILTIN_V2_MODULES:
            try:
                module = importlib.import_module(name)
                if hasattr(module, "artwork"):
                    result.update(module.artwork(manifest["id"], palette))
            except (ImportError, AttributeError, ValueError):
                continue
        stage = spec(manifest)
        if "header" not in result:
            scene = scene_svg(stage["scene"]["id"], palette, stage["scene"]["density"] == "lively")
            if scene:
                result["header"] = scene
        return result

    base = {key: value for key, value in manifest.items() if key not in ("stage", "palettes")}
    base.update(renderer_contract="1.2", assets=[])
    # V2 has its own compact chrome. The five older contracts retain their exact renderer.
    palettes = manifest.get("palettes", {})
    views = {mode: drawings(palettes.get(mode, base["skin"]["palette"])) for mode in ("dark", "light")}
    palette = base["skin"]["palette"]
    values = [f"--{name}:{palette[field]}" for field, names in ALIASES.items() for name in names]
    values += [f"--{name}:{value}" for name, value in _tokens(base["skin"]).items()]
    surface = ":is(#main,#sidebar,#side,#pane,#xpanel,.tb-root)" + GUARD
    result = surface + "{" + ";".join(values) + ";--bg2:var(--panel);--grad:var(--accent);" + "}\n"
    for mode in ("light", "dark"):
        if mode not in palettes or (mode == "dark" and palettes[mode] == base["skin"]["palette"]):
            continue
        palette = palettes[mode]
        values = [f"--{name}:{palette[field]}" for field, names in ALIASES.items() for name in names]
        values += [f"--{name}:{value}" for name, value in _tokens({**base['skin'], 'palette': palette}).items()]
        body = "color-scheme:" + mode + ";" + ";".join(values) + ";--bg2:var(--panel);--grad:var(--accent);"
        result += f':root[data-theme="{mode}"] {surface}{{{body}}}\n'
        result += f'@media(prefers-color-scheme:{mode}){{:root[data-theme="system"] {surface}{{{body}}}}}\n'
    # Large image values live only on their consumers, not on the whole app tree.
    for slot, selector in (("header", "#main > header"), ("empty", ".empty"), ("perch", "#tb-perch")):
        if not all(slot in views[mode] for mode in ("dark", "light")):
            continue
        definitions = ";".join(f"--tb-stage-{slot}-{mode}:{css_url(views[mode][slot])}" for mode in ("dark", "light"))
        # This inert direct child is application-owned; packages cannot create it.
        # Keep its image bindings off the guarded application-content selectors.
        def image_rule(body, slot=slot, selector=selector):
            if slot == "perch":
                return "body > #tb-perch[aria-hidden=true]{" + body + "}\n"
            return _rule((selector,), body)
        result += image_rule(definitions + f";--tb-stage-{slot}:var(--tb-stage-{slot}-dark)")
        light_rule = image_rule(f"--tb-stage-{slot}:var(--tb-stage-{slot}-light)")
        result += ':root[data-theme="light"] ' + light_rule
        result += '@media(prefers-color-scheme:light){:root[data-theme="system"] ' + light_rule + '}\n'
    if all("perch" in views[mode] for mode in ("dark", "light")):
        result += ":root{--tb-has-perch:1}\n"
        result += "body > #tb-perch[aria-hidden=true]{background:var(--tb-stage-perch) center/100% 100% no-repeat}\n"
    if all("empty" in views[mode] for mode in ("dark", "light")):
        result += _rule((".empty",), "margin-block-start:18px;width:min(100%,620px);max-width:620px")
        result += _rule((".empty::before",), 'content:"";display:block;height:190px;width:100%;background:var(--tb-stage-empty) center/contain no-repeat;pointer-events:none;margin-block-end:12px')
        result += _rule((".tb-mini",), "background:var(--panel)")
    result += _rule(("#sidebar", "#side", ".tb-card", ".set-section"), "background-color:var(--panel);color:var(--ink);border-color:var(--border)")
    result += _rule(("textarea", "input:not([type=checkbox])", "select", ".msg.user .msg-text"), f"background-color:var(--field);color:var(--ink);border-color:var(--border);caret-color:var(--{spec(manifest)['accents']['caret']})")
    result += _rule(("button", "input", "textarea", "select", "a"), "outline:3px solid var(--border);outline-offset:3px", ":focus-visible")
    # Conservatively exclude the whole alert, bypass and wallet namespaces in v2.
    # V1 keeps the exact original guard and its golden output.
    from core.toybox_fragment import TRUSTED

    exclusions = TRUSTED.replace('#bypassBanner,#bypassModal', '[id^="bypass"]')
    exclusions = exclusions.replace('[role="alertdialog"],[role="alert"]', '[role^="alert"]')
    exclusions = exclusions.replace('#vwSec,#vwCx,.vw-sheet,#vwHomeOverlay', '[id^="vw"],.vw-sheet')
    exclusions = exclusions.replace('.msg.is-private', '.is-private')
    exclusions = exclusions.replace('.private-divider,.private-note,.private-banner', '[class^="private-"],[class*=" private-"]')
    short_guard = ":not(:is(" + exclusions + ")):not(:is(" + exclusions + ") *)"
    return (result + css(manifest, bool(views["dark"])) + reactive_css(manifest)).replace(GUARD, short_guard) + ISLAND_RESET




def thumbnail(manifest):
    from core.toybox_materials import css_url

    p = manifest["skin"]["palette"]
    stage = spec(manifest)
    radius = {"book": 2, "cabinet": 3, "porthole": 28}.get(stage["frame"]["set"], 16)
    markup = (f"<svg xmlns='http://www.w3.org/2000/svg' width='320' height='200' viewBox='0 0 320 200'>"
              f"<rect width='320' height='200' fill='{p['background']}'/>"
              f"<rect x='8' y='8' width='304' height='184' rx='{radius}' fill='{p['panel']}' stroke='{p['border']}' stroke-width='4'/>"
              f"<path d='M68 14V186M68 46H304M68 154H304' stroke='{p['border']}' stroke-width='3'/>"
              f"<path d='M22 42H52M22 66H48M22 90H52M84 70H270M84 88H240M84 118H278' stroke='{p['muted']}' stroke-width='5'/>"
              f"<rect x='84' y='164' width='156' height='16' rx='4' fill='{p['field']}'/>"
              f"<rect x='250' y='160' width='40' height='24' rx='{radius // 2}' fill='{p['accent']}'/></svg>")
    return css_url(markup)[5:-2]


STAGE_JS = r"""(function(){
'use strict';
const root=document.documentElement, reduced=matchMedia('(prefers-reduced-motion: reduce)');
let saved='full', idleTimer=0, chunkAt=0, lastUpdate=0, leafCount=0;
const completedViews=new WeakSet();
let effect=null,effectTimer=0,effectTick=0,effectProfile='none',effectAccent='#b9d780',effectMuted='#aab0b8',effectUntil=0,layoutReady=false,railDrag=false;
const attr=(name,value)=>{if(root.dataset[name]!==value)root.dataset[name]=value;};
function governor(){
 const state=root.dataset.voolActivity||'unknown';
 const focused=typeof document.hasFocus==='function'&&document.hasFocus();
 attr('voolMotion',(reduced.matches||document.hidden||!focused||['approval','waiting','unknown','failure','cancelled'].includes(state))?'off':saved);
 paintEffect();
}
function activity(state,cadence){
 const allowed=['idle','starting','thinking','tool','streaming','waiting','approval','success','failure','cancelled','unknown'];
 state=allowed.includes(state)?state:'unknown';window.VoolToyboxChrome?.activity(state);
 if(state==='success'&&root.dataset.voolActivity!=='success')effectUntil=performance.now()+900;
 attr('voolActivity',state);
 const now=performance.now();
 if(now-lastUpdate>=100){const value=String(Math.round(Math.max(0,Math.min(1,Number(cadence)||0))*100)/100);if(root.style.getPropertyValue('--vool-cadence')!==value)root.style.setProperty('--vool-cadence',value);lastUpdate=now;}
 clearTimeout(idleTimer);attr('voolAmbient','on');
 idleTimer=setTimeout(()=>{attr('voolAmbient','off');paintEffect();},5000);governor();
}
function ensureLayout(){
 if(layoutReady)return;const sidebar=document.getElementById('sidebar'),handle=document.getElementById('sideResize'),pane=document.getElementById('xpanel');
 if(!sidebar||!handle)return;layoutReady=true;
 function rail(value){const n=Number.parseInt(value,10);if(Number.isFinite(n)&&n>=180&&n<=1000){const text=n+'px';if(root.style.getPropertyValue('--tb-user-rail')!==text)root.style.setProperty('--tb-user-rail',text);}}
 try{rail(localStorage.getItem('vool_sidebar_w'));}catch(_){}
 handle.addEventListener('mousedown',()=>{railDrag=true;});
 window.addEventListener('mousemove',()=>{if(railDrag)rail(sidebar.style.width);});
 window.addEventListener('mouseup',()=>{if(railDrag){rail(sidebar.style.width);railDrag=false;}});
 window.addEventListener('storage',e=>{if(e.key==='vool_sidebar_w')rail(e.newValue);});
 if(pane&&typeof ResizeObserver==='function')new ResizeObserver(()=>{const width=Math.round(pane.getBoundingClientRect().width)+'px';if(root.style.getPropertyValue('--tb-pane-width')!==width)root.style.setProperty('--tb-pane-width',width);}).observe(pane);
}
function ensureEffect(){
 if(typeof document.querySelector!=='function'||typeof getComputedStyle!=='function')return;
 const header=document.querySelector('#main > header');if(!header)return;
 ensureLayout();
 try{
  const style=getComputedStyle(header);effectProfile=style.getPropertyValue('--tb-profile').trim();
  effectAccent=style.getPropertyValue('--accent').trim()||effectAccent;effectMuted=style.getPropertyValue('--muted').trim()||effectMuted;
  if(!['meter','glow','sweep','bloom'].includes(effectProfile)){if(effect)effect.canvas.remove();effect=null;clearTimeout(effectTimer);return;}
  if(!effect){const canvas=document.createElement('canvas');canvas.id='tb-effect';canvas.width=104;canvas.height=24;canvas.setAttribute('aria-hidden','true');
   canvas.style.cssText='position:absolute;inset-block-start:10px;inset-inline-end:22px;width:104px;height:24px;pointer-events:none;z-index:0;contain:strict;will-change:transform;isolation:isolate';
   const g=canvas.getContext('2d');attr('tbCanvas','bounded');if(!g)return;header.append(canvas);effect={canvas,g};}
 }catch(_){clearTimeout(effectTimer);}
}
function paintEffect(){
 clearTimeout(effectTimer);if(!effect)return;
 const g=effect.g,state=root.dataset.voolActivity||'unknown',motion=root.dataset.voolMotion||'off';
 const active=['thinking','tool','streaming','success'].includes(state),ambient=state==='idle'&&root.dataset.voolAmbient==='on'&&motion==='full';
 const moving=motion!=='off'&&((active&&(state!=='success'||performance.now()<effectUntil))||ambient),tick=moving?effectTick++:0;
 const muted=['unknown','failure','cancelled','approval','waiting'].includes(state);
 const cadence=Math.max(0,Math.min(1,Number(root.style.getPropertyValue('--vool-cadence'))||0));
 g.clearRect(0,0,104,24);g.fillStyle=muted?effectMuted:effectAccent;g.strokeStyle=g.fillStyle;g.lineWidth=2;
 g.globalAlpha=muted?.35:state==='success'?.9:moving?.55+(tick%4)*.1:.5;
 if(root.dataset.skin==='dawn-circuit'){
  const blink=moving&&tick%32===15,smile=state==='success';
  for(const x of [20,72]){g.fillRect(x-11,4,22,16);g.clearRect(x-9,7,18,11);g.fillRect(x-7,7,5,blink?2:7);g.fillRect(x+2,7,5,blink?2:7);g.fillRect(x-3,16,6,smile?5:2);if(smile){g.fillRect(x-11,1,3,3);g.fillRect(x+8,1,3,3);}}
 }else if(effectProfile==='meter'){
  for(let i=0;i<8;i++){const h=muted?3:state==='streaming'?Math.round(3+cadence*(5+i*2)):state==='success'?6+i*2:3+(moving?(i+Math.floor(tick/2))%4:0);g.fillRect(i*12,23-h,8,h);}
 }else if(effectProfile==='sweep'){
  g.beginPath();g.arc(52,12,10,0,Math.PI*2);g.stroke();const angle=moving?tick*Math.PI/12:-Math.PI/2;g.beginPath();g.moveTo(52,12);g.lineTo(52+Math.cos(angle)*9,12+Math.sin(angle)*9);g.stroke();
  g.fillRect(19,10,2,2);g.fillRect(82,6,2,2);
 }else if(effectProfile==='bloom'){
  const count=root.dataset.skin==='glasshouse'?Math.min(12,leafCount):5;
  for(let i=0;i<count;i++){g.globalAlpha=muted?.25:state==='success'?1:state==='streaming'?(i<Math.ceil(cadence*5)?.9:.35):moving?(.4+((i+tick)%4)*.15):.6;g.fillRect(3+i*8,9-(i%2)*3,6,8);}
 }else{g.fillRect(1,10,102,4);if(moving)g.fillRect((tick*8)%96,6,8,12);}
 g.globalAlpha=1;
 if(moving&&!document.hidden)effectTimer=setTimeout(paintEffect,ambient?1000:125);
}
window.VoolStage={
 selection(s){saved=['full','calm','off'].includes(s.motion)?s.motion:'full';attr('voolLayout',s.layout_override==='standard'?'standard':'skin');ensureEffect();window.VoolToyboxChrome?.sync();governor();},
 mirror(chatId,streaming){
  try{
   const companion=window.VoolCompanion;
   if(!companion||!chatId){activity('unknown',0);return;}
   const view=companion.view(chatId), resolved=companion.resolve(view), state=resolved&&resolved.state;
   const map={IDLE:'idle',START:'starting',FINISH:'thinking',PASS:'success',FAILED:'failure',FAILED_FLASH:'failure',CANCELLED:'cancelled',APPROVAL_REQUIRED:'approval',UNKNOWN:'unknown',DONE_UNVERIFIED:'unknown'};
   let mapped=map[state]||'unknown';
   if(mapped==='success'&&!completedViews.has(view)){completedViews.add(view);leafCount++;root.style.setProperty('--tb-leaves',Math.min(leafCount,24));}
   else if(state!=='PASS')completedViews.delete(view);
   if(state==='MIDDLE')mapped=['RUNNING_TOOLS','READING_FILES','APPLYING_CHANGES','GIT_INSPECTION','TESTING_CI','WEB_RESEARCH'].includes(resolved.category)?'tool':resolved.category==='WAITING_QUEUED'?'waiting':'thinking';
   window.VoolPetCosmetics?.activity(mapped,chatId,resolved.category);
   const now=performance.now();
   const active=streaming&&!['approval','waiting','success','failure','cancelled','unknown'].includes(mapped);
   activity(active?'streaming':mapped,active?Math.min(1,100/Math.max(20,now-chunkAt)):0);chunkAt=now;
  }catch(_){activity('unknown',0);}
 },
};
for(const event of ['visibilitychange','focus','blur'])window.addEventListener(event,governor);
reduced.addEventListener('change',governor);activity('unknown',0);
})();"""
